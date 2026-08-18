from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FILE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_nonnegative_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


@dataclass(frozen=True)
class CacheRetentionPolicy:
    """Deletion policy for media artifacts stored under the persistent exports cache.

    All destructive behavior is opt-in. When CACHE_CLEANUP_ENABLED is absent or
    false, automatic cleanup is disabled regardless of the other values. A zero
    retention or size limit means that specific rule is disabled.
    """

    cleanup_enabled: bool = False
    retention_days: float = 0.0
    max_size_gb: float = 0.0
    cleanup_interval_hours: float = 24.0

    @classmethod
    def from_env(cls) -> "CacheRetentionPolicy":
        interval = _env_nonnegative_float("CACHE_CLEANUP_INTERVAL_HOURS", 24.0)
        if interval == 0:
            interval = 24.0
        return cls(
            cleanup_enabled=_env_bool("CACHE_CLEANUP_ENABLED", False),
            retention_days=_env_nonnegative_float("CACHE_RETENTION_DAYS", 0.0),
            max_size_gb=_env_nonnegative_float("CACHE_MAX_SIZE_GB", 0.0),
            cleanup_interval_hours=max(1.0 / 60.0, interval),
        )

    @property
    def max_size_bytes(self) -> int:
        return int(self.max_size_gb * 1024 * 1024 * 1024)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cleanup_enabled": self.cleanup_enabled,
            "retention_days": self.retention_days,
            "max_size_gb": self.max_size_gb,
            "cleanup_interval_hours": self.cleanup_interval_hours,
            "automatic_cleanup": "enabled" if self.cleanup_enabled else "disabled",
        }


class CacheRetentionManager:
    def __init__(self, exports: Path, policy: CacheRetentionPolicy | None = None) -> None:
        self.exports = exports.resolve()
        self.policy = policy or CacheRetentionPolicy.from_env()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    def _metadata_path(self, file_id: str) -> Path:
        return self.exports / f"{file_id}.json"

    def _read_metadata(self, file_id: str) -> dict[str, Any]:
        path = self._metadata_path(file_id)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_metadata(self, file_id: str, data: dict[str, Any]) -> None:
        path = self._metadata_path(file_id)
        tmp = self.exports / f".{file_id}.{os.getpid()}.retention.tmp"
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _media_paths(self) -> list[Path]:
        if not self.exports.is_dir():
            return []
        result: list[Path] = []
        for path in self.exports.glob("*__*"):
            if not path.is_file():
                continue
            file_id = path.name.split("__", 1)[0]
            if _FILE_ID_RE.fullmatch(file_id):
                result.append(path)
        return result

    def _entry(self, path: Path) -> dict[str, Any]:
        file_id, filename = path.name.split("__", 1)
        stat = path.stat()
        meta = self._read_metadata(file_id)
        retention = meta.get("retention") if isinstance(meta.get("retention"), dict) else {}
        created_raw = meta.get("created_epoch", int(stat.st_mtime))
        try:
            created_epoch = int(created_raw)
        except (TypeError, ValueError):
            created_epoch = int(stat.st_mtime)
        return {
            "file_id": file_id,
            "filename": filename,
            "path": path,
            "metadata_path": self._metadata_path(file_id),
            "size_bytes": int(stat.st_size),
            "created_epoch": created_epoch,
            "pinned": bool(retention.get("pinned", False)),
            "pin_note": str(retention.get("note", ""))[:500],
        }

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for path in self._media_paths():
                try:
                    rows.append(self._entry(path))
                except FileNotFoundError:
                    continue
            return rows

    def _plan(self, now_epoch: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now_epoch is None else int(now_epoch)
        rows = self.entries()
        selected: dict[str, dict[str, Any]] = {}
        reasons: dict[str, list[str]] = {}

        if self.policy.retention_days > 0:
            cutoff = now - int(self.policy.retention_days * 86400)
            for row in rows:
                if row["pinned"] or row["created_epoch"] >= cutoff:
                    continue
                selected[row["file_id"]] = row
                reasons.setdefault(row["file_id"], []).append("older_than_retention")

        remaining_bytes = sum(
            row["size_bytes"] for row in rows if row["file_id"] not in selected
        )
        max_bytes = self.policy.max_size_bytes
        if max_bytes > 0 and remaining_bytes > max_bytes:
            eligible = sorted(
                (
                    row
                    for row in rows
                    if not row["pinned"] and row["file_id"] not in selected
                ),
                key=lambda row: (row["created_epoch"], row["file_id"]),
            )
            for row in eligible:
                if remaining_bytes <= max_bytes:
                    break
                selected[row["file_id"]] = row
                reasons.setdefault(row["file_id"], []).append("cache_over_size_limit")
                remaining_bytes -= row["size_bytes"]

        candidates = []
        for row in sorted(
            selected.values(), key=lambda item: (item["created_epoch"], item["file_id"])
        ):
            candidates.append(
                {
                    "file_id": row["file_id"],
                    "filename": row["filename"],
                    "size_bytes": row["size_bytes"],
                    "created_epoch": row["created_epoch"],
                    "reasons": reasons.get(row["file_id"], []),
                }
            )

        return {
            "candidate_count": len(candidates),
            "candidate_bytes": sum(item["size_bytes"] for item in candidates),
            "candidates": candidates,
            "selected_rows": selected,
        }

    def status(self) -> dict[str, Any]:
        rows = self.entries()
        total_bytes = sum(row["size_bytes"] for row in rows)
        pinned = [row for row in rows if row["pinned"]]
        oldest = min((row["created_epoch"] for row in rows), default=None)
        newest = max((row["created_epoch"] for row in rows), default=None)
        plan = self._plan()
        return {
            "policy": self.policy.as_dict(),
            "file_count": len(rows),
            "total_bytes": total_bytes,
            "total_gib": round(total_bytes / (1024**3), 4),
            "pinned_count": len(pinned),
            "unpinned_count": len(rows) - len(pinned),
            "oldest_created_epoch": oldest,
            "newest_created_epoch": newest,
            "would_delete_count": plan["candidate_count"],
            "would_delete_bytes": plan["candidate_bytes"],
            "automatic_worker_running": bool(self._worker and self._worker.is_alive()),
            "backward_compatible_default": (
                "No automatic deletion occurs when CACHE_CLEANUP_ENABLED is absent/false."
            ),
        }

    def cleanup(self, *, dry_run: bool = True) -> dict[str, Any]:
        """Apply the configured age/size policy; pinned files are never selected."""
        with self._lock:
            plan = self._plan()
            candidates = plan["candidates"]
            if dry_run:
                return {
                    "dry_run": True,
                    "policy": self.policy.as_dict(),
                    "deleted_count": 0,
                    "deleted_bytes": 0,
                    "candidate_count": plan["candidate_count"],
                    "candidate_bytes": plan["candidate_bytes"],
                    "candidates": candidates[:200],
                    "candidates_truncated": len(candidates) > 200,
                }

            deleted: list[dict[str, Any]] = []
            selected_rows = plan["selected_rows"]
            for candidate in candidates:
                row = selected_rows[candidate["file_id"]]
                path: Path = row["path"]
                meta_path: Path = row["metadata_path"]
                try:
                    path.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                except OSError as exc:
                    candidate = {**candidate, "delete_error": str(exc)}
                    deleted.append(candidate)
                    continue
                deleted.append(candidate)

            successful = [item for item in deleted if "delete_error" not in item]
            return {
                "dry_run": False,
                "policy": self.policy.as_dict(),
                "deleted_count": len(successful),
                "deleted_bytes": sum(item["size_bytes"] for item in successful),
                "attempted_count": len(deleted),
                "errors": [item for item in deleted if "delete_error" in item][:50],
                "deleted": successful[:200],
                "deleted_truncated": len(successful) > 200,
            }

    def set_pinned(self, file_id: str, *, pinned: bool, note: str = "") -> dict[str, Any]:
        if not _FILE_ID_RE.fullmatch(file_id):
            raise ValueError("Invalid file_id")
        if len(note) > 500:
            raise ValueError("pin note may not exceed 500 characters")
        with self._lock:
            matches = list(self.exports.glob(f"{file_id}__*"))
            media = next((path for path in matches if path.is_file()), None)
            if media is None:
                raise ValueError("Cached file not found")
            meta = self._read_metadata(file_id)
            if not meta:
                stat = media.stat()
                meta = {
                    "file_id": file_id,
                    "filename": media.name.split("__", 1)[-1],
                    "size_bytes": stat.st_size,
                    "created_epoch": int(stat.st_mtime),
                }
            retention = meta.get("retention") if isinstance(meta.get("retention"), dict) else {}
            retention = dict(retention)
            retention["pinned"] = bool(pinned)
            retention["updated_epoch"] = int(time.time())
            if note:
                retention["note"] = note
            elif not pinned:
                retention.pop("note", None)
            meta["retention"] = retention
            self._write_metadata(file_id, meta)
            return {
                "file_id": file_id,
                "filename": media.name.split("__", 1)[-1],
                "pinned": bool(pinned),
                "note": str(retention.get("note", "")),
            }

    def _worker_main(self) -> None:
        # Cleanup immediately after startup, then periodically. No worker is
        # started at all unless cleanup is explicitly enabled.
        while not self._stop_event.is_set():
            try:
                self.cleanup(dry_run=False)
            except Exception:
                # Retention is maintenance; a cleanup failure must not terminate
                # the MCP process. The next interval retries.
                pass
            self._stop_event.wait(self.policy.cleanup_interval_hours * 3600)

    def start_worker(self) -> bool:
        if not self.policy.cleanup_enabled:
            return False
        with self._lock:
            if self._worker and self._worker.is_alive():
                return True
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._worker_main,
                name="video-mcp-cache-retention",
                daemon=True,
            )
            self._worker.start()
            return True

    def stop_worker(self) -> None:
        self._stop_event.set()


def register_cache_retention_tools(mcp: Any, *, exports: Path) -> CacheRetentionManager:
    manager = CacheRetentionManager(exports=exports)

    @mcp.tool()
    async def cache_status() -> dict[str, Any]:
        """Inspect persistent MCP media-cache size and the active retention policy.

        Automatic deletion is OFF by default and stays off when the retention
        environment variables are absent. Check this tool before assuming old
        files will disappear. Pinned files are protected from all retention
        cleanup.
        """
        return manager.status()

    @mcp.tool()
    async def cache_cleanup(dry_run: bool = True) -> dict[str, Any]:
        """Apply the configured cache-retention policy; defaults to a safe dry run.

        The policy uses CACHE_RETENTION_DAYS and/or CACHE_MAX_SIZE_GB. This tool
        can be invoked even when automatic cleanup is disabled, but it still uses
        the configured age/size rules. Pinned files are never deleted. Call with
        dry_run=true first to inspect candidates, then dry_run=false to delete.
        """
        return manager.cleanup(dry_run=dry_run)

    @mcp.tool()
    async def cache_pin(file_id: str, note: str = "") -> dict[str, Any]:
        """Protect an important cached artifact from retention cleanup.

        `file_id` must belong to THIS MCP Video Gen cache. Pin project assets,
        long-lived references, .blend files, or other artifacts that must survive
        age/size cleanup.
        """
        return manager.set_pinned(file_id, pinned=True, note=note)

    @mcp.tool()
    async def cache_unpin(file_id: str) -> dict[str, Any]:
        """Remove retention protection from a cached artifact."""
        return manager.set_pinned(file_id, pinned=False)

    return manager
