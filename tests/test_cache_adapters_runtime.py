from __future__ import annotations

import asyncio
import base64
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


def test_cached_image_uploads_directly_without_client_round_trip(tmp_path):
    payload = b"not-a-real-png-but-binary-safe"
    cached_path = tmp_path / ("a" * 32 + "__render.png")
    cached_path.write_bytes(payload)
    calls = []

    async def comfy(method, path, **kwargs):
        assert method == "GET"
        assert path == "object_info"
        return {"LoadImage": {}}

    async def upload_image_base64(filename, data_base64, overwrite=False, subfolder=""):
        calls.append((filename, data_base64, overwrite, subfolder))
        return {"name": filename, "subfolder": subfolder, "type": "input"}

    server = SimpleNamespace(
        comfy=comfy,
        upload_image_base64=upload_image_base64,
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
    assert len(calls) == 1
    assert calls[0][0] == "render.png"
    assert base64.b64decode(calls[0][1]) == payload
    assert calls[0][2] is True
    assert calls[0][3] == "blender"


def test_cached_image_adapter_soft_fails_when_comfyui_is_unreachable(tmp_path):
    cached_path = tmp_path / ("b" * 32 + "__render.png")
    cached_path.write_bytes(b"data")

    async def comfy(*args, **kwargs):
        raise ConnectionError("connection refused")

    async def upload_image_base64(*args, **kwargs):
        raise AssertionError("upload must not be attempted while unavailable")

    server = SimpleNamespace(
        comfy=comfy,
        upload_image_base64=upload_image_base64,
        COMFY_URL="http://host.docker.internal:8188",
    )
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(mcp.tools["comfy_upload_cached_image"]("b" * 32))
    assert result["ok"] is False
    assert result["available"] is False
    assert result["backend"] == "comfyui"
    assert result["status"] == "unavailable"
