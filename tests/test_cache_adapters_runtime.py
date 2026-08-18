from __future__ import annotations

import asyncio
from types import SimpleNamespace

from video_mcp.cache_adapters import register_cache_adapters


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def test_cached_image_uploads_directly_without_base64_or_client_round_trip(tmp_path):
    payload = b"not-a-real-png-but-binary-safe"
    cached_path = tmp_path / ("a" * 32 + "__render.png")
    cached_path.write_bytes(payload)
    calls = []

    async def comfy(method, path, **kwargs):
        if method == "GET":
            calls.append((method, path, kwargs))
            assert path == "object_info"
            return {"LoadImage": {}}
        assert method == "POST"
        assert path == "upload/image"
        filename, uploaded, content_type = kwargs["files"]["image"]
        calls.append(
            (
                method,
                path,
                {
                    "data": kwargs["data"],
                    "filename": filename,
                    "uploaded_bytes": uploaded.read(),
                    "content_type": content_type,
                },
            )
        )
        return {"name": "render.png", "subfolder": "blender", "type": "input"}

    server = SimpleNamespace(
        comfy=comfy,
        COMFY_URL="http://host.docker.internal:8188",
    )
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(
        mcp.tools["comfy_upload_cached_image"]("a" * 32, overwrite=True, subfolder="blender")
    )
    assert result["ok"] is True
    assert result["status"] == "uploaded"
    assert result["source_file_id"] == "a" * 32
    assert result["workflow_load_image_value"] == "blender/render.png"
    assert result["comfyui_input"] == {
        "name": "render.png",
        "subfolder": "blender",
        "type": "input",
    }
    assert "do not pass the original URL" in result["next_step"]

    assert len(calls) == 2
    method, path, captured = calls[1]
    assert method == "POST"
    assert path == "upload/image"
    assert captured["data"] == {"overwrite": "true", "subfolder": "blender"}
    assert captured["filename"] == "render.png"
    assert captured["uploaded_bytes"] == payload
    assert captured["content_type"] == "image/png"


def test_cached_image_adapter_rejects_non_image_file(tmp_path):
    cached_path = tmp_path / ("c" * 32 + "__clip.mp4")
    cached_path.write_bytes(b"video")
    calls = []

    async def comfy(method, path, **kwargs):
        calls.append((method, path))
        return {"LoadImage": {}}

    server = SimpleNamespace(
        comfy=comfy,
        COMFY_URL="http://host.docker.internal:8188",
    )
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(mcp.tools["comfy_upload_cached_image"]("c" * 32))
    assert result["ok"] is False
    assert result["available"] is True
    assert result["status"] == "operation_failed"
    assert "only accepts image files" in result["message"]
    assert calls == [("GET", "object_info")]


def test_cached_image_adapter_soft_fails_when_comfyui_is_unreachable(tmp_path):
    cached_path = tmp_path / ("b" * 32 + "__render.png")
    cached_path.write_bytes(b"data")

    async def comfy(*args, **kwargs):
        raise ConnectionError("connection refused")

    server = SimpleNamespace(
        comfy=comfy,
        COMFY_URL="http://host.docker.internal:8188",
    )
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(mcp.tools["comfy_upload_cached_image"]("b" * 32))
    assert result["ok"] is False
    assert result["available"] is False
    assert result["backend"] == "comfyui"
    assert result["status"] == "unavailable"
