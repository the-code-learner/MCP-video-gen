from __future__ import annotations

import base64
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable


def register_file_transfer_tools(
    mcp: Any,
    *,
    exports: Path,
    tmp: Path,
    cached: Callable[[str], Path],
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    max_upload_mb: int,
    max_inline_mb: int,
) -> None:
    """Register generic client<->MCP cache transfer tools.

    Files imported through these tools become normal cache entries and can be
    consumed by ComfyUI, Blender, FFmpeg, HyperFrames, timelines, and the other
    media tools. Chunked transfer avoids requiring one very large MCP result or
    request for binary files.
    """

    upload_root = (tmp / "client-uploads").resolve()

    def ensure_upload_root() -> None:
        upload_root.mkdir(parents=True, exist_ok=True)

    def state_path(upload_id: str) -> Path:
        if len(upload_id) != 32 or any(c not in "0123456789abcdef" for c in upload_id.lower()):
            raise ValueError("Invalid upload_id")
        return upload_root / f"{upload_id}.json"

    def part_path(upload_id: str) -> Path:
        return upload_root / f"{upload_id}.part"

    def read_state(upload_id: str) -> dict[str, Any]:
        path = state_path(upload_id)
        if not path.is_file():
            raise ValueError("Upload session not found")
        return json.loads(path.read_text(encoding="utf-8"))

    @mcp.tool()
    async def cache_file_base64(
        filename: str,
        data_base64: str,
        source: str = "mcp-client",
    ) -> dict[str, Any]:
        """Import a binary file from the MCP client into the persistent cache."""
        data = base64.b64decode(data_base64, validate=True)
        if len(data) > max_upload_mb * 1024 * 1024:
            raise ValueError("Upload exceeds MAX_UPLOAD_MB; use chunked upload or increase the configured limit")
        file_id, out = target(filename)
        out.write_bytes(data)
        return file_meta(file_id, out, source, sha256=hashlib.sha256(data).hexdigest())

    @mcp.tool()
    async def cache_text_file(
        filename: str,
        text: str,
        source: str = "mcp-client",
    ) -> dict[str, Any]:
        """Import UTF-8 text authored by the client/AI into the persistent cache."""
        data = text.encode("utf-8")
        if len(data) > max_upload_mb * 1024 * 1024:
            raise ValueError("Text upload exceeds MAX_UPLOAD_MB")
        file_id, out = target(filename)
        out.write_bytes(data)
        return file_meta(file_id, out, source, sha256=hashlib.sha256(data).hexdigest())

    @mcp.tool()
    async def file_upload_begin(
        filename: str,
        expected_size_bytes: int = 0,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        """Begin a chunked binary upload from the MCP client/AI to the server cache."""
        if expected_size_bytes < 0:
            raise ValueError("expected_size_bytes must be >= 0")
        limit = max_upload_mb * 1024 * 1024
        if expected_size_bytes and expected_size_bytes > limit:
            raise ValueError("Expected upload exceeds MAX_UPLOAD_MB")
        if expected_sha256 and (len(expected_sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256)):
            raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
        ensure_upload_root()
        upload_id = uuid.uuid4().hex
        state = {
            "upload_id": upload_id,
            "filename": Path(filename).name,
            "expected_size_bytes": expected_size_bytes,
            "expected_sha256": expected_sha256.lower(),
        }
        if not state["filename"]:
            raise ValueError("Invalid filename")
        part_path(upload_id).write_bytes(b"")
        state_path(upload_id).write_text(json.dumps(state, indent=2), encoding="utf-8")
        return {**state, "next_offset": 0, "max_upload_bytes": limit}

    @mcp.tool()
    async def file_upload_chunk(
        upload_id: str,
        offset_bytes: int,
        data_base64: str,
    ) -> dict[str, Any]:
        """Append one base64 chunk to an in-progress upload; offsets must be contiguous."""
        state = read_state(upload_id)
        part = part_path(upload_id)
        current = part.stat().st_size
        if offset_bytes != current:
            raise ValueError(f"offset_bytes must equal next_offset {current}")
        data = base64.b64decode(data_base64, validate=True)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("A single chunk may not exceed 4 MiB decoded")
        new_size = current + len(data)
        limit = max_upload_mb * 1024 * 1024
        if new_size > limit:
            raise ValueError("Upload exceeds MAX_UPLOAD_MB")
        expected = int(state.get("expected_size_bytes") or 0)
        if expected and new_size > expected:
            raise ValueError("Upload exceeds expected_size_bytes")
        with part.open("ab") as handle:
            handle.write(data)
        return {"upload_id": upload_id, "received_bytes": len(data), "next_offset": new_size}

    @mcp.tool()
    async def file_upload_finish(upload_id: str) -> dict[str, Any]:
        """Validate and promote a chunked upload into the persistent media cache."""
        state = read_state(upload_id)
        part = part_path(upload_id)
        size = part.stat().st_size
        expected_size = int(state.get("expected_size_bytes") or 0)
        if expected_size and size != expected_size:
            raise ValueError(f"Upload size mismatch: expected {expected_size}, got {size}")
        digest = hashlib.sha256()
        with part.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        actual_sha = digest.hexdigest()
        expected_sha = str(state.get("expected_sha256") or "")
        if expected_sha and actual_sha != expected_sha:
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")
        file_id, out = target(str(state["filename"]))
        shutil.move(str(part), str(out))
        state_path(upload_id).unlink(missing_ok=True)
        return file_meta(file_id, out, "mcp-client", sha256=actual_sha, chunked_upload=True)

    @mcp.tool()
    async def file_upload_abort(upload_id: str) -> dict[str, Any]:
        """Discard an incomplete client upload."""
        state_path(upload_id).unlink(missing_ok=True)
        part_path(upload_id).unlink(missing_ok=True)
        return {"upload_id": upload_id, "aborted": True}

    @mcp.tool()
    async def get_cached_file_info(file_id: str) -> dict[str, Any]:
        """Return cache metadata and transfer options for one file."""
        path = cached(file_id)
        metadata_path = exports / f"{file_id}.json"
        if metadata_path.is_file():
            try:
                return json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "file_id": file_id,
            "filename": path.name.split("__", 1)[-1],
            "size_bytes": path.stat().st_size,
            "download_path": f"/files/{file_id}",
        }

    @mcp.tool()
    async def read_cached_file_chunk_base64(
        file_id: str,
        offset_bytes: int = 0,
        length_bytes: int = 1024 * 1024,
    ) -> dict[str, Any]:
        """Read a bounded binary chunk from the MCP cache for transfer back to the AI/client."""
        path = cached(file_id)
        size = path.stat().st_size
        if offset_bytes < 0 or offset_bytes > size:
            raise ValueError("offset_bytes is outside the file")
        max_chunk = min(max_inline_mb * 1024 * 1024, 4 * 1024 * 1024)
        length = max(1, min(length_bytes, max_chunk))
        with path.open("rb") as handle:
            handle.seek(offset_bytes)
            data = handle.read(length)
        next_offset = offset_bytes + len(data)
        return {
            "file_id": file_id,
            "filename": path.name.split("__", 1)[-1],
            "offset_bytes": offset_bytes,
            "length_bytes": len(data),
            "next_offset": next_offset,
            "size_bytes": size,
            "eof": next_offset >= size,
            "data_base64": base64.b64encode(data).decode("ascii"),
        }
