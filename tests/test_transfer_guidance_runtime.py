from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path

from video_mcp.file_transfer import MAX_CHUNK_DECODED_BYTES, register_file_transfer_tools
from video_mcp.routing_guide import replace_file_transfer_guide
from video_mcp.server_instructions import SERVER_INSTRUCTIONS


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *args, name=None, **kwargs):
        def decorator(fn):
            self.tools[name or fn.__name__] = fn
            return fn

        return decorator

    def remove_tool(self, name):
        self.tools.pop(name, None)


def _register_transfer_tools(tmp_path: Path) -> FakeMCP:
    exports = tmp_path / "exports"
    tmp = tmp_path / "tmp"
    exports.mkdir()
    tmp.mkdir()

    def target(filename: str):
        file_id = uuid.uuid4().hex
        return file_id, exports / f"{file_id}__{Path(filename).name}"

    def cached(file_id: str):
        return next(exports.glob(f"{file_id}__*"))

    def file_meta(file_id: str, path: Path, source: str, **details):
        return {
            "file_id": file_id,
            "filename": path.name.split("__", 1)[-1],
            "size_bytes": path.stat().st_size,
            "source": source,
            "details": details,
        }

    mcp = FakeMCP()
    register_file_transfer_tools(
        mcp,
        exports=exports,
        tmp=tmp,
        cached=cached,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
        max_inline_mb=8,
    )
    return mcp


def test_chunk_tools_tell_clients_to_avoid_tiny_round_trips(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    payload = b"a" * 89_458

    begun = asyncio.run(
        mcp.tools["file_upload_begin"](
            "speech.wav",
            expected_size_bytes=len(payload),
        )
    )
    assert begun["max_chunk_decoded_bytes"] == MAX_CHUNK_DECODED_BYTES
    assert begun["recommended_next_chunk_bytes"] == len(payload)
    assert "cache_file_base64" in begun["transfer_guidance"]
    assert "KB-sized" in begun["transfer_guidance"]

    uploaded = asyncio.run(
        mcp.tools["file_upload_chunk"](
            begun["upload_id"],
            0,
            base64.b64encode(payload).decode("ascii"),
        )
    )
    assert uploaded["remaining_bytes"] == 0
    assert uploaded["next_action"] == "file_upload_finish(upload_id)"
    assert uploaded["recommended_next_chunk_bytes"] == 0


def test_tool_docstrings_explain_one_shot_vs_chunked_selection(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    one_shot = mcp.tools["cache_file_base64"].__doc__ or ""
    begin = mcp.tools["file_upload_begin"].__doc__ or ""
    chunk = mcp.tools["file_upload_chunk"].__doc__ or ""

    assert "single call" in one_shot
    assert "small" in one_shot
    assert "4 MiB" in begin
    assert "full MCP/LLM round trip" in chunk
    assert "KB-sized" in chunk


def test_routing_guide_contains_transfer_efficiency_decision_tree():
    mcp = FakeMCP()
    mcp.tools["file_transfer_guide"] = lambda: None
    replace_file_transfer_guide(mcp)

    guide = asyncio.run(mcp.tools["file_transfer_guide"]())
    assert guide["client_to_cache_decision"][0]["action"] == "import_remote_file(uri)"
    assert "cache_file_base64" in guide["client_to_cache_decision"][1]["action"]
    assert guide["chunking"]["max_decoded_bytes_per_call"] == 4 * 1024 * 1024
    assert "KB-sized" in guide["chunking"]["anti_pattern"]


def test_server_instructions_reject_intentionally_tiny_chunks():
    assert "never intentionally split a small KB-sized file" in SERVER_INSTRUCTIONS
    assert "up to 4 MiB decoded per call" in SERVER_INSTRUCTIONS
