from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from video_mcp.file_transfer import register_file_transfer_tools
from video_mcp.routing_guide import replace_file_transfer_guide
from video_mcp.server_instructions import SERVER_INSTRUCTIONS


REMOVED_BINARY_UPLOAD_TOOLS = {
    "cache_file_base64",
    "file_upload_begin",
    "file_upload_status",
    "file_upload_chunk_auto",
    "file_upload_chunk",
    "file_upload_finish",
    "file_upload_abort",
}


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
    exports.mkdir()

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
        cached=cached,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
        max_inline_mb=8,
    )
    return mcp


def test_binary_model_mediated_upload_tools_are_removed(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    assert REMOVED_BINARY_UPLOAD_TOOLS.isdisjoint(mcp.tools)
    assert set(mcp.tools) == {
        "cache_text_file",
        "get_cached_file_info",
        "read_cached_file_chunk_base64",
    }


def test_cache_text_file_remains_for_model_authored_text(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    result = asyncio.run(mcp.tools["cache_text_file"]("notes.txt", "hello"))
    assert result["filename"] == "notes.txt"
    assert result["size_bytes"] == 5


def test_routing_guide_prefers_native_chatgpt_file_params():
    mcp = FakeMCP()
    mcp.tools["file_transfer_guide"] = lambda: None
    replace_file_transfer_guide(mcp)

    guide = asyncio.run(mcp.tools["file_transfer_guide"]())
    first = guide["client_to_cache_decision"][0]
    assert "attached in ChatGPT" in first["condition"]
    assert "save_uploaded_file" in first["action"]
    assert guide["native_chatgpt_upload"]["tool_meta"] == "openai/fileParams"
    assert guide["native_chatgpt_upload"]["binary_through_model_context"] is False
    assert set(guide["removed_binary_upload_tools"]) == REMOVED_BINARY_UPLOAD_TOOLS
    assert guide["client_to_cache_decision"][1]["action"] == "import_remote_file(uri)"


def test_server_instructions_define_native_attachment_route_and_reject_chunking():
    assert "save_uploaded_file(file)" in SERVER_INSTRUCTIONS
    assert "openai/fileParams" in SERVER_INSTRUCTIONS
    assert "Do NOT manually base64-encode or chunk the attachment" in SERVER_INSTRUCTIONS
    assert "file_upload_chunk_auto" not in SERVER_INSTRUCTIONS
    assert "Programmatic Tool Calling" not in SERVER_INSTRUCTIONS
