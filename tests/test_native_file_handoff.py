from __future__ import annotations

import asyncio

from mcp.types import ResourceLink

from video_mcp.native_handoff import register_native_file_handoff


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}
        self.resources = {}

    def tool(self, *args, name=None, **kwargs):
        def decorator(fn):
            self.tools[name or fn.__name__] = fn
            return fn

        return decorator

    def resource(self, uri, *args, **kwargs):
        def decorator(fn):
            self.resources[uri] = fn
            return fn

        return decorator


def test_resource_link_prefers_https_stream_without_embedding_bytes(tmp_path):
    file_id = "a" * 32
    payload = b"binary-media-payload"
    path = tmp_path / f"{file_id}__frame.png"
    path.write_bytes(payload)

    mcp = FakeMCP()
    register_native_file_handoff(
        mcp,
        cached=lambda requested: path if requested == file_id else None,
        public_base_url="https://media.example.test",
    )

    link = asyncio.run(mcp.tools["get_cached_file_resource"](file_id))
    assert isinstance(link, ResourceLink)
    assert str(link.uri) == f"https://media.example.test/files/{file_id}"
    assert link.name == "frame.png"
    assert link.mime_type == "image/png"
    assert link.size == len(payload)
    assert not hasattr(link, "data")
    assert not hasattr(link, "blob")

    resource = mcp.resources["media://cache/{file_id}"]
    assert asyncio.run(resource(file_id)) == payload


def test_resource_link_falls_back_to_mcp_resource_without_public_url(tmp_path):
    file_id = "b" * 32
    path = tmp_path / f"{file_id}__scene.glb"
    path.write_bytes(b"glb")

    mcp = FakeMCP()
    register_native_file_handoff(
        mcp,
        cached=lambda requested: path if requested == file_id else None,
        public_base_url="",
    )

    link = asyncio.run(mcp.tools["get_cached_file_resource"](file_id))
    assert isinstance(link, ResourceLink)
    assert str(link.uri) == f"media://cache/{file_id}"
    assert link.name == "scene.glb"
    assert link.mime_type in {"model/gltf-binary", "application/octet-stream"}
    assert link.size == 3
