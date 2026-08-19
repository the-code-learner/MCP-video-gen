from __future__ import annotations

import base64
import binascii
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable


MAX_CHUNK_DECODED_BYTES = 4 * 1024 * 1024
RECOMMENDED_CHUNK_DECODED_BYTES = 1024 * 1024


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
    """Register compatibility transfer tools around the canonical MCP cache.

    Routing order for AI clients:
    1. Prefer `import_remote_file(uri)` when a real server-retrievable HTTPS URL exists.
    2. If the complete file is already available as base64 and fits one tool call,
       prefer one `cache_file_base64(...)` call, especially for small files.
    3. Use chunked upload only when one-shot transfer is not practical. If the client
       supports Programmatic Tool Calling (PTC), run the complete deterministic
       begin -> chunk loop -> finish stage programmatically without returning to the
       language model between chunks.
    4. Prefer `get_cached_file_resource`/HTTP streaming for cache -> client delivery.

    Files imported here become normal cache entries usable by ComfyUI, Blender,
    FFmpeg, HyperFrames and the other media tools through local `file_id` values.
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

    def progress(state: dict[str, Any], current: int) -> dict[str, Any]:
        expected = int(state.get("expected_size_bytes") or 0)
        remaining = max(0, expected - current) if expected else None
        return {
            "upload_id": state["upload_id"],
            "filename": state["filename"],
            "total_received_bytes": current,
            "expected_size_bytes": expected or None,
            "remaining_bytes": remaining,
            "complete_by_size": bool(expected and current == expected),
            "max_chunk_decoded_bytes": MAX_CHUNK_DECODED_BYTES,
        }

    @mcp.tool()
    async def cache_file_base64(
        filename: str,
        data_base64: str,
        source: str = "mcp-client",
    ) -> dict[str, Any]:
        """Import one complete base64 file into the Video Gen cache in a single call.

        USE THIS instead of chunked upload when all file bytes/base64 are already
        available and the payload comfortably fits one tool call. This is strongly
        preferred for small files: do NOT split a small audio/image/file into many
        model-mediated chunk calls.

        If the client/tool layer rejects or truncates the one-shot payload, fall back
        to one chunked session. When Programmatic Tool Calling is available and the
        program can access the source bytes, perform the whole chunk loop with
        `file_upload_chunk_auto` inside one programmatic stage rather than returning
        to the language model between chunks.

        Do NOT choose base64 when the client exposes a real server-retrievable HTTPS
        URL; use `import_remote_file(uri)` instead because it streams server-side.

        After caching, reuse the returned local `file_id`; do not re-encode or
        re-upload the same unchanged artifact.
        """
        data = base64.b64decode(data_base64, validate=True)
        if len(data) > max_upload_mb * 1024 * 1024:
            raise ValueError("Upload exceeds MAX_UPLOAD_MB; use chunked upload or increase the configured limit")
        file_id, out = target(filename)
        out.write_bytes(data)
        meta = file_meta(file_id, out, source, sha256=hashlib.sha256(data).hexdigest())
        return {
            **meta,
            "transfer_complete": True,
            "do_not_reencode_or_reupload": True,
            "next_action": "Reuse this file_id for subsequent Video Gen operations",
            "comfyui_next_action": "comfy_upload_cached_media(file_id)",
        }

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
        """Begin one chunked upload only when one-shot transfer is impractical.

        Prefer `import_remote_file(uri)` for a retrievable HTTPS URL and
        `cache_file_base64` when the complete payload fits one call. If one-shot was
        actually rejected/truncated, create ONE chunked session for the unchanged
        source file; do not repeatedly restart unless validation fails.

        PTC: if the client supports Programmatic Tool Calling and its program can
        access the source bytes, use `file_upload_chunk_auto` in a deterministic
        loop and then `file_upload_finish`, without a model turn between chunks.
        `file_upload_chunk_auto` is preferred for PTC because the server owns the
        offset and rejects malformed chunks atomically before writing them.
        """
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
        recommended = (
            min(expected_size_bytes, MAX_CHUNK_DECODED_BYTES)
            if expected_size_bytes
            else RECOMMENDED_CHUNK_DECODED_BYTES
        )
        return {
            **state,
            "next_offset": 0,
            "max_upload_bytes": limit,
            "max_chunk_decoded_bytes": MAX_CHUNK_DECODED_BYTES,
            "recommended_next_chunk_bytes": recommended,
            "preferred_chunk_tool": "file_upload_chunk_auto",
            "programmatic_tool_calling": {
                "recommended_when_supported": True,
                "bounded_stage": "file_upload_begin -> repeated file_upload_chunk_auto -> file_upload_finish",
                "no_model_turn_between_chunks": True,
                "status_tool": "file_upload_status",
            },
            "transfer_guidance": (
                "Use one session and the largest practical chunks. Prefer file_upload_chunk_auto so "
                "the server owns offsets and validates each chunk before append. If PTC is available, "
                "run the complete chunk loop programmatically without returning to the model."
            ),
        }

    @mcp.tool()
    async def file_upload_status(upload_id: str) -> dict[str, Any]:
        """Return authoritative server-side progress for an active chunked upload.

        This is safe to call from a Programmatic Tool Calling loop for recovery or
        validation. The server's current `.part` size is the source of truth; the
        client does not need to maintain or calculate offsets itself.
        """
        state = read_state(upload_id)
        part = part_path(upload_id)
        return {
            **progress(state, part.stat().st_size),
            "expected_sha256": state.get("expected_sha256") or None,
            "preferred_chunk_tool": "file_upload_chunk_auto",
        }

    @mcp.tool()
    async def file_upload_chunk_auto(
        upload_id: str,
        data_base64: str,
        expected_decoded_bytes: int = 0,
    ) -> dict[str, Any]:
        """Atomically append the next chunk; the SERVER owns the offset.

        This is the preferred chunk tool for deterministic/programmatic upload loops.
        Do not calculate or send an offset. Optionally set `expected_decoded_bytes`
        to the exact raw-byte length the client intended for this chunk. The server
        decodes and validates the complete chunk BEFORE writing anything.

        On invalid base64, decoded-size mismatch, per-chunk limit violation, or a
        chunk that would exceed expected_size_bytes, the result has `accepted=false`
        and `file_unchanged=true`; retry the same source chunk or abort. A rejected
        chunk never advances server progress.

        When PTC is available, repeatedly call this tool from the same program and
        stop when `complete_by_size=true`, then call `file_upload_finish(upload_id)`.
        Do not return to the language model between successful chunks.
        """
        state = read_state(upload_id)
        part = part_path(upload_id)
        current = part.stat().st_size
        before = progress(state, current)
        if expected_decoded_bytes < 0:
            return {
                **before,
                "accepted": False,
                "file_unchanged": True,
                "reason": "expected_decoded_bytes_must_be_nonnegative",
                "next_action": "retry_same_chunk",
            }
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError):
            return {
                **before,
                "accepted": False,
                "file_unchanged": True,
                "reason": "invalid_base64",
                "next_action": "retry_same_chunk",
            }
        decoded = len(data)
        if expected_decoded_bytes and decoded != expected_decoded_bytes:
            return {
                **before,
                "accepted": False,
                "file_unchanged": True,
                "reason": "decoded_size_mismatch",
                "decoded_bytes": decoded,
                "expected_decoded_bytes": expected_decoded_bytes,
                "next_action": "retry_same_chunk",
            }
        if decoded > MAX_CHUNK_DECODED_BYTES:
            return {
                **before,
                "accepted": False,
                "file_unchanged": True,
                "reason": "chunk_exceeds_max_decoded_bytes",
                "decoded_bytes": decoded,
                "next_action": "retry_with_smaller_chunk",
            }
        if current + decoded > max_upload_mb * 1024 * 1024:
            return {
                **before,
                "accepted": False,
                "file_unchanged": True,
                "reason": "upload_exceeds_max_upload_bytes",
                "decoded_bytes": decoded,
                "next_action": "abort_upload",
            }
        expected = int(state.get("expected_size_bytes") or 0)
        if expected and current + decoded > expected:
            return {
                **before,
                "accepted": False,
                "file_unchanged": True,
                "reason": "chunk_would_exceed_expected_size",
                "decoded_bytes": decoded,
                "next_action": "retry_same_chunk_with_correct_source_range",
            }
        with part.open("ab") as handle:
            handle.write(data)
        after = progress(state, current + decoded)
        return {
            **after,
            "accepted": True,
            "file_unchanged": False,
            "received_bytes": decoded,
            "expected_decoded_bytes": expected_decoded_bytes or None,
            "next_action": (
                "file_upload_finish(upload_id)"
                if after["complete_by_size"]
                else "send_next_chunk_with_file_upload_chunk_auto"
            ),
        }

    @mcp.tool()
    async def file_upload_chunk(
        upload_id: str,
        offset_bytes: int,
        data_base64: str,
    ) -> dict[str, Any]:
        """Append one contiguous chunk using an explicit compatibility offset.

        Prefer `file_upload_chunk_auto` for new clients and Programmatic Tool Calling:
        it removes offset arithmetic from the model/client and validates a chunk
        atomically before append. Keep this explicit-offset tool only for compatibility
        with clients that already implement the older protocol.

        `offset_bytes` MUST equal the `next_offset` returned by the previous call.
        Maximum decoded chunk size is 4 MiB. After the final bytes are uploaded, call
        `file_upload_finish(upload_id)`.
        """
        state = read_state(upload_id)
        part = part_path(upload_id)
        current = part.stat().st_size
        if offset_bytes != current:
            raise ValueError(f"offset_bytes must equal next_offset {current}")
        data = base64.b64decode(data_base64, validate=True)
        if len(data) > MAX_CHUNK_DECODED_BYTES:
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

        remaining = max(0, expected - new_size) if expected else None
        recommended = (
            min(remaining, MAX_CHUNK_DECODED_BYTES)
            if remaining is not None
            else RECOMMENDED_CHUNK_DECODED_BYTES
        )
        return {
            "upload_id": upload_id,
            "received_bytes": len(data),
            "next_offset": new_size,
            "expected_size_bytes": expected or None,
            "remaining_bytes": remaining,
            "max_chunk_decoded_bytes": MAX_CHUNK_DECODED_BYTES,
            "recommended_next_chunk_bytes": recommended,
            "next_action": (
                "file_upload_finish(upload_id)"
                if remaining == 0
                else "prefer file_upload_chunk_auto for the next chunk"
            ),
        }

    @mcp.tool()
    async def file_upload_finish(upload_id: str) -> dict[str, Any]:
        """Validate and promote a completed chunked upload into the persistent cache.

        Call this immediately after the final accepted chunk. If
        `expected_size_bytes` was supplied to `file_upload_begin`, finish succeeds
        only when that exact byte count has arrived. If expected SHA-256 was supplied,
        it must match exactly. The returned `file_id` is then canonical and should be
        reused without re-encoding/re-uploading the unchanged artifact.
        """
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
        meta = file_meta(file_id, out, "mcp-client", sha256=actual_sha, chunked_upload=True)
        return {
            **meta,
            "transfer_complete": True,
            "do_not_reencode_or_reupload": True,
            "next_action": "Reuse this file_id for subsequent Video Gen operations",
            "comfyui_next_action": "comfy_upload_cached_media(file_id)",
        }

    @mcp.tool()
    async def file_upload_abort(upload_id: str) -> dict[str, Any]:
        """Discard an incomplete compatibility client upload."""
        state_path(upload_id).unlink(missing_ok=True)
        part_path(upload_id).unlink(missing_ok=True)
        return {"upload_id": upload_id, "aborted": True}

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
        delivery. Do not reconstruct large media through repeated model-mediated
        chunk calls when a native/resource reference is available. If chunked read
        is unavoidable, request large chunks rather than many KB-sized reads.
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
