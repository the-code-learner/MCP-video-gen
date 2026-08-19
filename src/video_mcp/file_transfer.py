from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


MAX_CHUNK_DECODED_BYTES = 4 * 1024 * 1024


def register_file_transfer_tools(
    mcp: Any,
    *,
    exports: Path,
    cached: Callable[[str], Path],
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    max_upload_mb: int,
    max_inline_mb: int,
) -> None:
    """Register non-binary-authoring and compatibility read tools for the cache.

    Binary client -> cache ingress is intentionally not implemented here. ChatGPT
    attachments use the native `save_uploaded_file` / `save_uploaded_files` tools
    declared with `_meta["openai/fileParams"]`; arbitrary retrievable HTTPS sources
    use `import_remote_file`. This avoids model-mediated base64/chunk upload loops.
    """

    @mcp.tool()
    async def cache_text_file(
        filename: str,
        text: str,
        source: str = "mcp-client",
    ) -> dict[str, Any]:
        """Import UTF-8 text authored by the client/AI into the persistent cache.

        This is for text created in the conversation/workflow, not for transporting
        attached binary media. For ChatGPT attachments use `save_uploaded_file`.
        """
        data = text.encode("utf-8")
        if len(data) > max_upload_mb * 1024 * 1024:
            raise ValueError("Text upload exceeds MAX_UPLOAD_MB")
        file_id, out = target(filename)
        out.write_bytes(data)
        return file_meta(file_id, out, source, sha256=hashlib.sha256(data).hexdigest())

    @mcp.tool()
    async def get_cached_file_info(file_id: str) -> dict[str, Any]:
        """Return cache metadata and transfer options for one local file_id."""
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
        """Compatibility/debug fallback: read a cached-file chunk as base64.

        Prefer `get_cached_file_resource(file_id)` and HTTP streaming for normal
        delivery. This tool is outbound-only and is not an upload mechanism.
        """
        path = cached(file_id)
        size = path.stat().st_size
        if offset_bytes < 0 or offset_bytes > size:
            raise ValueError("offset_bytes is outside the file")
        max_chunk = min(max_inline_mb * 1024 * 1024, MAX_CHUNK_DECODED_BYTES)
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
