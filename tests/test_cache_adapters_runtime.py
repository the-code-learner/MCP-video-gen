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


def _server_and_calls():
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
        return {"name": filename, "subfolder": kwargs["data"]["subfolder"], "type": "input"}

    return SimpleNamespace(comfy=comfy, COMFY_URL="http://host.docker.internal:8188"), calls


def test_cached_image_uploads_directly_without_base64_or_client_round_trip(tmp_path):
    payload = b"not-a-real-png-but-binary-safe"
    cached_path = tmp_path / ("a" * 32 + "__render.png")
    cached_path.write_bytes(payload)
    server, calls = _server_and_calls()
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(
        mcp.tools["comfy_upload_cached_image"]("a" * 32, overwrite=True, subfolder="blender")
    )
    assert result["ok"] is True
    assert result["status"] == "uploaded"
    assert result["source_file_id"] == "a" * 32
    assert result["workflow_input_value"] == "blender/render.png"
    assert result["workflow_load_image_value"] == "blender/render.png"
    assert result["media_kind"] == "image"
    assert result["comfyui_input"] == {
        "name": "render.png",
        "subfolder": "blender",
        "type": "input",
    }
    assert "LoadImage" in result["next_step"]

    assert len(calls) == 2
    method, path, captured = calls[1]
    assert method == "POST"
    assert path == "upload/image"
    assert captured["data"] == {
        "overwrite": "true",
        "subfolder": "blender",
        "type": "input",
    }
    assert captured["filename"] == "render.png"
    assert captured["uploaded_bytes"] == payload
    assert captured["content_type"] == "image/png"


def test_generic_media_adapter_stages_video_and_returns_node_specific_guidance(tmp_path):
    payload = b"video-binary"
    cached_path = tmp_path / ("c" * 32 + "__clip.mp4")
    cached_path.write_bytes(payload)
    server, calls = _server_and_calls()
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(mcp.tools["comfy_upload_cached_media"]("c" * 32, subfolder="incoming"))
    assert result["ok"] is True
    assert result["status"] == "staged"
    assert result["media_kind"] == "video"
    assert result["content_type"] == "video/mp4"
    assert result["workflow_input_value"] == "incoming/clip.mp4"
    assert "workflow_load_image_value" not in result
    assert "get_node_definition" in result["next_step"]
    assert calls[1][2]["uploaded_bytes"] == payload


def test_cached_image_adapter_rejects_non_image_and_points_to_generic_staging(tmp_path):
    cached_path = tmp_path / ("c" * 32 + "__clip.mp4")
    cached_path.write_bytes(b"video")
    calls = []

    async def comfy(method, path, **kwargs):
        calls.append((method, path))
        return {"LoadImage": {}}

    server = SimpleNamespace(comfy=comfy, COMFY_URL="http://host.docker.internal:8188")
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(mcp.tools["comfy_upload_cached_image"]("c" * 32))
    assert result["ok"] is False
    assert result["available"] is True
    assert result["status"] == "operation_failed"
    assert "only accepts image files" in result["message"]
    assert "comfy_upload_cached_media" in result["message"]
    assert calls == []


def test_cached_media_adapter_soft_fails_when_comfyui_is_unreachable(tmp_path):
    cached_path = tmp_path / ("b" * 32 + "__render.png")
    cached_path.write_bytes(b"data")

    async def comfy(*args, **kwargs):
        raise ConnectionError("connection refused")

    server = SimpleNamespace(comfy=comfy, COMFY_URL="http://host.docker.internal:8188")
    mcp = FakeMCP()
    register_cache_adapters(mcp, server_module=server, cached=lambda _file_id: cached_path)

    result = asyncio.run(mcp.tools["comfy_upload_cached_media"]("b" * 32))
    assert result["ok"] is False
    assert result["available"] is False
    assert result["backend"] == "comfyui"
    assert result["status"] == "unavailable"
