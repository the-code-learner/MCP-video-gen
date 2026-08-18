from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.utilities.func_metadata import func_metadata
from mcp.types import ResourceLink
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from video_mcp import server
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

    tool = mcp.tools["get_cached_file_resource"]
    link = asyncio.run(tool(file_id))
    assert isinstance(link, ResourceLink)
    assert str(link.uri) == f"https://media.example.test/files/{file_id}"
    assert link.name == "frame.png"
    assert link.mime_type == "image/png"
    assert link.size == len(payload)
    assert not hasattr(link, "data")
    assert not hasattr(link, "blob")

    converted = func_metadata(tool).convert_result(link)
    assert len(converted.content) == 1
    assert isinstance(converted.content[0], ResourceLink)
    assert str(converted.content[0].uri) == f"https://media.example.test/files/{file_id}"

    mcp_link = asyncio.run(tool(file_id, transport="mcp"))
    assert str(mcp_link.uri) == f"media://cache/{file_id}"

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

    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        asyncio.run(mcp.tools["get_cached_file_resource"](file_id, transport="http"))


def test_http_file_route_supports_range_requests(tmp_path, monkeypatch):
    file_id = "c" * 32
    payload = b"0123456789abcdef"
    path = tmp_path / f"{file_id}__clip.mp4"
    path.write_bytes(payload)

    def fake_cached(requested: str):
        if requested != file_id:
            raise ValueError("not found")
        return path

    monkeypatch.setattr(server, "cached", fake_cached)
    app = Starlette(routes=[Route("/files/{file_id}", server.download, methods=["GET", "HEAD"])])

    with TestClient(app) as client:
        head = client.head(f"/files/{file_id}")
        assert head.status_code == 200
        assert head.headers["content-length"] == str(len(payload))
        assert head.headers["accept-ranges"] == "bytes"
        assert "clip.mp4" in head.headers["content-disposition"]

        partial = client.get(f"/files/{file_id}", headers={"Range": "bytes=4-9"})
        assert partial.status_code == 206
        assert partial.content == payload[4:10]
        assert partial.headers["content-range"] == f"bytes 4-9/{len(payload)}"
