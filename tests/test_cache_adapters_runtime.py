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
        calls.append((method, path, kwargs))
        if method == "GET":
            assert path == "object_info"
            return {"LoadImage": {}}
        assert method == "POST"
        assert path == "upload/image"
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

    assert len(calls) == 2
    method, path, kwargs = calls[1]
    assert method == "POST"
    assert path == "upload/image"
    assert kwargs["data"] == {"overwrite": "true", "subfolder": "blender"}
    filename, uploaded, content_type = kwargs["files"]["image"]
    assert filename == "render.png"
    assert uploaded == payload
    assert content_type == "image/png"


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
