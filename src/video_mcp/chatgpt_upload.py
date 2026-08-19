from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import mimetypes
import os
import re
import shutil
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NotRequired, TypedDict
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import ConfigDict


_REDIRECT_CODES = {301, 302, 303, 307, 308}
_STREAM_CHUNK_BYTES = 64 * 1024


class OpenAIFile(TypedDict):
    """ChatGPT file-param object supplied for _meta['openai/fileParams']."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    download_url: str
    file_id: str
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


@dataclass(frozen=True)
class DownloadedFile:
    size_bytes: int
    sha256: str
    response_media_type: str | None
    source_host: str


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(1, min(value, maximum))


def chatgpt_file_timeout_seconds() -> int:
    return _positive_int_env("CHATGPT_FILE_TIMEOUT_SEC", 300, 1800)


def chatgpt_file_max_redirects() -> int:
    return _positive_int_env("CHATGPT_FILE_MAX_REDIRECTS", 5, 10)


def chatgpt_file_max_batch_files() -> int:
    return _positive_int_env("CHATGPT_FILE_MAX_BATCH_FILES", 20, 100)


async def _resolved_addresses(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve ChatGPT file host: {hostname}") from exc
    addresses = {ipaddress.ip_address(info[4][0]) for info in infos if info and info[4]}
    if not addresses:
        raise ValueError(f"ChatGPT file host resolved to no addresses: {hostname}")
    return addresses


async def validate_public_https_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw or len(raw) > 8192:
        raise ValueError("ChatGPT file download_url is empty or too long")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise ValueError("ChatGPT file download_url must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("ChatGPT file download_url must not contain credentials")
    if not parsed.hostname:
        raise ValueError("ChatGPT file download_url has no hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ChatGPT file download_url has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("ChatGPT file download_url must use HTTPS port 443")
    addresses = await _resolved_addresses(parsed.hostname)
    blocked = sorted(str(address) for address in addresses if not address.is_global)
    if blocked:
        raise ValueError(
            f"ChatGPT file host resolves to a non-public address: {', '.join(blocked)}"
        )
    return parsed.hostname


def _safe_filename(value: str) -> str:
    raw = str(value or "").strip().replace("\x00", "")
    if not raw:
        return ""
    raw = raw.replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        return ""
    return name[:255]


def filename_for_openai_file(source: OpenAIFile, override: str = "") -> str:
    explicit = _safe_filename(override)
    if override and not explicit:
        raise ValueError("filename is invalid")
    if explicit:
        return explicit

    supplied = _safe_filename(str(source.get("file_name") or ""))
    if supplied:
        return supplied

    file_id = str(source.get("file_id") or "").strip()
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", file_id).strip("._")[:96] or "chatgpt-file"
    mime_type = str(source.get("mime_type") or "").split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(mime_type) if mime_type else None
    return f"{safe_id}{extension or '.bin'}"


async def download_openai_file_to_path(
    source: OpenAIFile,
    destination: Path,
    *,
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
) -> DownloadedFile:
    """Stream one ChatGPT-authorized temporary file URL to disk with SSRF/size limits."""
    if not isinstance(source, dict):
        raise ValueError("file must be a ChatGPT file object")
    url = str(source.get("download_url") or "").strip()
    file_id = str(source.get("file_id") or "").strip()
    if not file_id or len(file_id) > 512:
        raise ValueError("file_id is required and must be at most 512 characters")

    maximum = max(1, int(max_bytes))
    redirects_left = chatgpt_file_max_redirects()
    own_client = client is None
    if own_client:
        timeout = httpx.Timeout(float(chatgpt_file_timeout_seconds()), connect=20.0)
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
    assert client is not None

    digest = hashlib.sha256()
    total = 0
    media_type: str | None = None
    source_host = ""
    try:
        while True:
            source_host = await validate_public_https_url(url)
            async with client.stream(
                "GET",
                url,
                headers={"User-Agent": "MCP-Video-Gen/native-chatgpt-file", "Accept": "*/*"},
            ) as response:
                if response.status_code in _REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("ChatGPT file redirect has no Location header")
                    if redirects_left <= 0:
                        raise ValueError("ChatGPT file exceeded redirect limit")
                    redirects_left -= 1
                    url = urljoin(url, location)
                    continue

                if response.status_code < 200 or response.status_code >= 300:
                    raise ValueError(
                        f"ChatGPT file download failed with HTTP {response.status_code}"
                    )

                declared = response.headers.get("content-length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError:
                        declared_size = -1
                    if declared_size > maximum:
                        raise ValueError(
                            f"ChatGPT file exceeds configured MAX_UPLOAD_MB ({maximum} bytes)"
                        )

                media_type = response.headers.get("content-type")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as handle:
                    async for chunk in response.aiter_bytes(_STREAM_CHUNK_BYTES):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > maximum:
                            raise ValueError(
                                f"ChatGPT file exceeds configured MAX_UPLOAD_MB ({maximum} bytes)"
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                break
    except httpx.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise ValueError(f"ChatGPT file download failed: {type(exc).__name__}") from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if own_client:
            await client.aclose()

    return DownloadedFile(
        size_bytes=total,
        sha256=digest.hexdigest(),
        response_media_type=media_type,
        source_host=source_host,
    )


def register_chatgpt_upload_tools(
    mcp: Any,
    *,
    tmp: Path,
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    max_upload_mb: int,
) -> None:
    """Register native ChatGPT attachment ingress using openai/fileParams."""

    native_root = (tmp / "chatgpt-file-inputs").resolve()
    maximum = max_upload_mb * 1024 * 1024

    async def save_one(file: OpenAIFile, filename: str = "") -> dict[str, Any]:
        resolved_name = filename_for_openai_file(file, filename)
        native_root.mkdir(parents=True, exist_ok=True)
        part = native_root / f"{uuid.uuid4().hex}.part"
        downloaded = await download_openai_file_to_path(file, part, max_bytes=maximum)
        file_id, out = target(resolved_name)
        try:
            shutil.move(str(part), str(out))
        except Exception:
            part.unlink(missing_ok=True)
            raise

        source_mime = str(file.get("mime_type") or "").strip()
        meta = file_meta(
            file_id,
            out,
            "chatgpt-file-input",
            sha256=downloaded.sha256,
            native_chatgpt_file_upload=True,
            source_file_id=str(file.get("file_id") or ""),
            source_mime_type=source_mime or None,
            response_content_type=downloaded.response_media_type,
            source_host=downloaded.source_host,
        )
        return {
            **meta,
            "transfer_complete": True,
            "do_not_reencode_or_reupload": True,
            "next_action": "Reuse this file_id for subsequent Video Gen operations",
            "comfyui_next_action": "comfy_upload_cached_media(file_id)",
        }

    @mcp.tool(meta={
        "openai/fileParams": ["file"],
        "openai/toolInvocation/invoking": "Saving uploaded media",
        "openai/toolInvocation/invoked": "Uploaded media saved",
    })
    async def save_uploaded_file(
        file: OpenAIFile,
        filename: str = "",
    ) -> dict[str, Any]:
        """Save one file attached in ChatGPT directly into the Video Gen cache.

        This is the preferred ChatGPT attachment-ingress path. The tool declares
        `_meta["openai/fileParams"]`, allowing ChatGPT to supply a temporary
        authorized `download_url` and file metadata directly to the MCP tool. The
        server then streams the original bytes from that URL into its cache; binary
        content does not pass through the language model as base64.

        Use the returned `file_id` for all later Video Gen operations. Do not manually
        construct a file object when the client can bind an attached file natively.
        """
        return await save_one(file, filename)

    @mcp.tool(meta={
        "openai/fileParams": ["files"],
        "openai/toolInvocation/invoking": "Saving uploaded media files",
        "openai/toolInvocation/invoked": "Uploaded media files processed",
    })
    async def save_uploaded_files(
        files: list[OpenAIFile],
    ) -> dict[str, Any]:
        """Save several ChatGPT-attached files directly into the Video Gen cache.

        Successful files remain cached if another item fails. Each source is streamed
        server-side from its temporary authorized download URL and is independently
        bounded by MAX_UPLOAD_MB. The batch count is bounded by
        CHATGPT_FILE_MAX_BATCH_FILES.
        """
        limit = chatgpt_file_max_batch_files()
        if len(files) > limit:
            raise ValueError(f"files exceeds CHATGPT_FILE_MAX_BATCH_FILES ({limit})")

        results: list[dict[str, Any]] = []
        saved = 0
        failed = 0
        for item in files:
            try:
                result = await save_one(item)
                results.append({"ok": True, **result})
                saved += 1
            except Exception as exc:
                results.append({
                    "ok": False,
                    "source_file_id": str(item.get("file_id") or "") if isinstance(item, dict) else "",
                    "file_name": str(item.get("file_name") or "") if isinstance(item, dict) else "",
                    "error": str(exc),
                })
                failed += 1

        return {
            "ok": failed == 0,
            "saved_count": saved,
            "failed_count": failed,
            "files": results,
        }


# Temporary authorized file URLs are bearer-like capabilities. The generic httpx
# INFO logger includes full URLs (including query strings), so keep it above INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
