from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from video_mcp import file_ingress
from video_mcp.file_ingress import (
    _validate_remote_url,
    install_comfy_workflow_input_guard,
    register_file_ingress_tools,
)


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *args, name=None, **kwargs):
        def decorator(fn):
            self.tools[name or fn.__name__] = fn
            return fn

        return decorator


def cache_helpers(root: Path):
    exports = root / "exports"
    tmp = root / "tmp"
    exports.mkdir(parents=True)
    tmp.mkdir(parents=True)

    def target(filename: str):
        file_id = uuid.uuid4().hex
        return file_id, exports / f"{file_id}__{Path(filename).name}"

    def file_meta(file_id: str, path: Path, source: str, **details):
        meta = {
            "file_id": file_id,
            "filename": path.name.split("__", 1)[-1],
            "size_bytes": path.stat().st_size,
            "source": source,
            "details": details,
        }
        (exports / f"{file_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    return exports, tmp, target, file_meta


def test_remote_import_streams_to_cache_without_base64(tmp_path, monkeypatch):
    exports, tmp, target, file_meta = cache_helpers(tmp_path)
    mcp = FakeMCP()
    register_file_ingress_tools(
        mcp,
        tmp=tmp,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
    )

    payload = (b"png-binary-payload" * 10000) + b"end"
    original_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://files.example.test/reference.png"
        return httpx.Response(
            200,
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(len(payload)),
            },
            content=payload,
        )

    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return original_async_client(transport=transport, **kwargs)

    async def validated(uri: str, allowed_hosts):
        assert uri.startswith("https://")
        return "files.example.test"

    monkeypatch.setattr(file_ingress.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(file_ingress, "_validate_remote_url", validated)

    result = asyncio.run(
        mcp.tools["import_remote_file"](
            "https://files.example.test/reference.png",
            expected_size_bytes=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    )

    stored = list(exports.glob(f"{result['file_id']}__*"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == payload
    assert result["filename"] == "reference.png"
    assert result["details"]["remote_import"] is True
    assert result["details"]["source_host"] == "files.example.test"


def test_remote_import_requires_https_without_embedded_credentials():
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(_validate_remote_url("http://example.com/file.png", ()))
    with pytest.raises(ValueError, match="credentials"):
        asyncio.run(_validate_remote_url("https://user:pass@example.com/file.png", ()))


def test_remote_import_rejects_non_public_dns_result(monkeypatch):
    monkeypatch.setattr(
        file_ingress.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="non-public address"):
        asyncio.run(_validate_remote_url("https://example.test/file.png", ()))


def test_remote_import_optional_host_allowlist(monkeypatch):
    monkeypatch.setattr(
        file_ingress.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(
            _validate_remote_url(
                "https://files.example.test/file.png",
                ("allowed.example.test",),
            )
        )


def test_standard_loadimage_rejects_urls_and_mcp_file_ids():
    calls = []

    async def submit_workflow(workflow, client_id=""):
        calls.append((workflow, client_id))
        return {"prompt_id": "ok"}

    server = SimpleNamespace(submit_workflow=submit_workflow)
    install_comfy_workflow_input_guard(server)

    with pytest.raises(ValueError, match="Never pass an HTTP"):
        asyncio.run(
            server.submit_workflow(
                {
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "https://example.test/reference.png"},
                    }
                }
            )
        )

    with pytest.raises(ValueError, match="MCP file_id"):
        asyncio.run(
            server.submit_workflow(
                {
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "a" * 32},
                    }
                }
            )
        )

    result = asyncio.run(
        server.submit_workflow(
            {"1": {"class_type": "LoadImage", "inputs": {"image": "input/reference.png"}}},
            "client-1",
        )
    )
    assert result == {"prompt_id": "ok"}
    assert len(calls) == 1


def test_transfer_guide_is_explicit_about_comfyui_routes(tmp_path):
    _exports, tmp, target, file_meta = cache_helpers(tmp_path)
    mcp = FakeMCP()
    register_file_ingress_tools(
        mcp,
        tmp=tmp,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
    )

    guide = asyncio.run(mcp.tools["file_transfer_guide"]())
    assert guide["canonical_identifier"] == "file_id inside the MCP cache"
    assert guide["routes"]["cache_image_to_comfyui_input"]["tool"].startswith(
        "comfy_upload_cached_image"
    )
    assert guide["routes"]["cache_audio_or_video_to_comfyui"]["status"].startswith(
        "no generic"
    )
    assert any("Never pass HTTP" in rule for rule in guide["golden_rules"])
