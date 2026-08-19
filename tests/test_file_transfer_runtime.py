from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from video_mcp.external_backends import register_external_backend_tools
from video_mcp.file_transfer import register_file_transfer_tools


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *args, name=None, **kwargs):
        def decorator(fn):
            self.tools[name or fn.__name__] = fn
            return fn

        return decorator

    def add_tool(self, fn, name=None, **kwargs):
        self.tools[name or fn.__name__] = fn
        return fn

    def remove_tool(self, name):
        self.tools.pop(name, None)


def cache_helpers(root: Path):
    exports = root / "exports"
    tmp = root / "tmp"
    exports.mkdir(parents=True)
    tmp.mkdir(parents=True)

    def target(filename: str):
        file_id = uuid.uuid4().hex
        return file_id, exports / f"{file_id}__{Path(filename).name}"

    def cached(file_id: str):
        matches = list(exports.glob(f"{file_id}__*"))
        if not matches:
            raise ValueError("not found")
        return matches[0]

    def file_meta(file_id: str, path: Path, source: str, **details):
        meta = {
            "file_id": file_id,
            "filename": path.name.split("__", 1)[-1],
            "size_bytes": path.stat().st_size,
            "source": source,
            "download_path": f"/files/{file_id}",
            "details": details,
        }
        (exports / f"{file_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    return exports, tmp, target, cached, file_meta


def test_text_authoring_and_outbound_chunk_read_round_trip(tmp_path):
    exports, _tmp, target, cached, file_meta = cache_helpers(tmp_path)
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

    text = "MCP Video Gen native file routing\n" * 20_000
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    saved = asyncio.run(mcp.tools["cache_text_file"]("notes.txt", text))
    assert saved["size_bytes"] == len(encoded)
    assert saved["details"]["sha256"] == digest
    file_id = saved["file_id"]

    rebuilt = bytearray()
    offset = 0
    while True:
        part = asyncio.run(
            mcp.tools["read_cached_file_chunk_base64"](
                file_id,
                offset_bytes=offset,
                length_bytes=333_333,
            )
        )
        rebuilt.extend(base64.b64decode(part["data_base64"]))
        offset = part["next_offset"]
        if part["eof"]:
            break

    assert bytes(rebuilt) == encoded
    assert hashlib.sha256(rebuilt).hexdigest() == digest
    info = asyncio.run(mcp.tools["get_cached_file_info"](file_id))
    assert info["download_path"] == f"/files/{file_id}"
    assert "cache_file_base64" not in mcp.tools
    assert "file_upload_begin" not in mcp.tools
    assert "file_upload_chunk" not in mcp.tools


def test_unreachable_comfyui_and_disabled_blender_are_model_readable(tmp_path, monkeypatch):
    exports, _tmp, target, cached, file_meta = cache_helpers(tmp_path)
    mcp = FakeMCP()

    async def unreachable_comfy(*args, **kwargs):
        raise ConnectionError("connection refused")

    async def placeholder(*args, **kwargs):
        return {"unexpected": True}

    server = SimpleNamespace(
        comfy=unreachable_comfy,
        COMFY_URL="http://host.docker.internal:8188",
        MODELS=tmp_path / "no-models",
        NODES=tmp_path / "no-nodes",
    )
    for name in (
        "list_loaded_nodes",
        "get_node_definition",
        "list_model_folders",
        "list_registered_models",
        "submit_workflow",
        "get_queue",
        "get_history",
        "interrupt",
        "upload_image_base64",
        "cache_output",
        "inventory_summary",
    ):
        setattr(server, name, placeholder)
        mcp.tools[name] = placeholder

    monkeypatch.setenv("BLENDER_ENABLED", "false")
    monkeypatch.delenv("BLENDER_BRIDGE_TOKEN", raising=False)
    register_external_backend_tools(
        mcp,
        server_module=server,
        cached=cached,
        target=target,
        file_meta=file_meta,
    )

    nodes = asyncio.run(mcp.tools["list_loaded_nodes"]())
    assert nodes["ok"] is False
    assert nodes["available"] is False
    assert nodes["backend"] == "comfyui"
    assert nodes["status"] == "unavailable"

    blender = asyncio.run(mcp.tools["blender_info"]())
    assert blender["ok"] is False
    assert blender["available"] is False
    assert blender["backend"] == "blender"
    assert blender["status"] == "unavailable"

    inventory = asyncio.run(mcp.tools["inventory_summary"]())
    assert inventory["external_backends"]["comfyui"]["available"] is False
    assert inventory["external_backends"]["blender"]["available"] is False
