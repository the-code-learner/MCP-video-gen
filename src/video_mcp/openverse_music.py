from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

import httpx

from .download_utils import download_verified

OPENVERSE_AUDIO_ENDPOINT = "https://api.openverse.org/v1/audio/"
COMMERCIAL_LICENSES = {"cc0", "pdm", "by"}


class OpenverseSession:
    def __init__(self) -> None:
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def store(self, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            for row in rows:
                result_id = str(row.get("id", ""))
                if result_id:
                    self._results[result_id] = dict(row)
            if len(self._results) > 500:
                for key in list(self._results)[:-500]:
                    self._results.pop(key, None)

    def get(self, result_id: str) -> dict[str, Any]:
        with self._lock:
            if result_id not in self._results:
                raise ValueError("Unknown/expired Openverse result_id; run music_search again")
            return dict(self._results[result_id])


def _duration_ms(raw: Any) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _duration_matches(duration_ms: int | None, minimum_sec: int, maximum_sec: int) -> bool:
    if minimum_sec <= 0 and maximum_sec <= 0:
        return True
    if duration_ms is None:
        return False
    minimum_ms = max(0, minimum_sec) * 1000
    maximum_ms = max(0, maximum_sec) * 1000
    if minimum_ms and duration_ms < minimum_ms:
        return False
    if maximum_ms and duration_ms > maximum_ms:
        return False
    return True


def _safe_extension(value: Any) -> str:
    raw = str(value or "").strip().lower().lstrip(".")
    if re.fullmatch(r"[a-z0-9]{1,8}", raw):
        return "." + raw
    return ".audio"


async def search_music(
    session: OpenverseSession,
    *,
    query: str,
    limit: int = 10,
    commercial_use: bool = True,
    duration_min_sec: int = 0,
    duration_max_sec: int = 0,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(20, int(limit)))
    duration_min_sec = max(0, int(duration_min_sec))
    duration_max_sec = max(0, int(duration_max_sec))
    if duration_max_sec and duration_min_sec > duration_max_sec:
        raise ValueError("duration_min_sec cannot exceed duration_max_sec")

    # Openverse's `length` search parameter is categorical (shortest/short/
    # medium/long), while the response `duration` is an integer in milliseconds.
    # Exact user-provided second bounds are therefore applied locally.
    page_size = 50 if (duration_min_sec or duration_max_sec) else max(20, limit)
    max_pages = 4 if (duration_min_sec or duration_max_sec) else 2
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            params: dict[str, str | int] = {
                "q": query,
                "page_size": page_size,
                "page": page,
                "category": "music",
            }
            if commercial_use:
                params["license_type"] = "commercial"
            response = await client.get(OPENVERSE_AUDIO_ENDPOINT, params=params)
            response.raise_for_status()
            payload = response.json()
            raw_results = payload.get("results", []) if isinstance(payload, dict) else []
            if not raw_results:
                break
            for raw in raw_results:
                if not isinstance(raw, dict):
                    continue
                result_id = str(raw.get("id", ""))
                if not result_id or result_id in seen:
                    continue
                license_code = str(raw.get("license", "")).lower()
                if commercial_use and license_code not in COMMERCIAL_LICENSES:
                    continue
                url = str(raw.get("url", ""))
                if not url.startswith("https://"):
                    continue
                duration_ms = _duration_ms(raw.get("duration"))
                if not _duration_matches(duration_ms, duration_min_sec, duration_max_sec):
                    continue
                seen.add(result_id)
                rows.append(
                    {
                        "id": result_id,
                        "title": raw.get("title"),
                        "creator": raw.get("creator"),
                        "creator_url": raw.get("creator_url"),
                        "url": url,
                        "foreign_landing_url": raw.get("foreign_landing_url"),
                        "license": license_code,
                        "license_version": raw.get("license_version"),
                        "license_url": raw.get("license_url"),
                        "attribution": raw.get("attribution"),
                        "source": raw.get("source"),
                        "provider": raw.get("provider"),
                        "duration_ms": duration_ms,
                        "duration_sec": round(duration_ms / 1000, 3) if duration_ms is not None else None,
                        "filetype": raw.get("filetype"),
                        "filesize": raw.get("filesize"),
                        "genres": raw.get("genres"),
                    }
                )
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break

    session.store(rows)
    return {
        "query": query,
        "commercial_use": commercial_use,
        "duration_min_sec": duration_min_sec,
        "duration_max_sec": duration_max_sec,
        "results": rows,
        "count": len(rows),
        "license_note": "Openverse aggregates license metadata. Verify the original source landing page before publication; Video Gen preserves attribution and license fields with imported media.",
        "duration_note": "Openverse response duration is milliseconds; exact second bounds are applied locally by Video Gen.",
    }


async def import_music(
    session: OpenverseSession,
    *,
    result_id: str,
    filename: str,
    tmp: Path,
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    row = session.get(result_id)
    requested = Path(filename).name if filename else ""
    if not requested:
        requested = hashlib.sha256(result_id.encode("utf-8")).hexdigest()[:24] + _safe_extension(row.get("filetype"))
    if not requested or requested in {".", ".."}:
        raise ValueError("invalid output filename")

    max_bytes = max(1, int(os.getenv("MUSIC_MAX_DOWNLOAD_MB", "250"))) * 1024 * 1024
    tmp_root = tmp / "openverse"
    tmp_root.mkdir(parents=True, exist_ok=True)
    safe_id = hashlib.sha256(result_id.encode("utf-8")).hexdigest()
    local = tmp_root / f"{safe_id}-{requested}"
    try:
        download = await download_verified(str(row["url"]), local, max_bytes=max_bytes)
        content_type = str(download.get("content_type") or "").lower()
        if content_type and not (
            content_type.startswith("audio/")
            or content_type in {"application/octet-stream", "binary/octet-stream"}
        ):
            raise ValueError(f"Openverse media URL returned non-audio content type: {content_type}")
        file_id, out = target(requested)
        local.replace(out)
        return file_meta(
            file_id,
            out,
            "openverse.audio",
            openverse_id=result_id,
            title=row.get("title"),
            creator=row.get("creator"),
            license=row.get("license"),
            license_version=row.get("license_version"),
            license_url=row.get("license_url"),
            attribution=row.get("attribution"),
            foreign_landing_url=row.get("foreign_landing_url"),
            provider=row.get("provider"),
            source=row.get("source"),
            duration_ms=row.get("duration_ms"),
            sha256=download["sha256"],
            response_content_type=content_type,
        )
    finally:
        local.unlink(missing_ok=True)
