from __future__ import annotations

import asyncio
import ipaddress
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp.server import MCPServer

from video_mcp.chatgpt_upload import (
    DownloadedFile,
    download_openai_file_to_path,
    register_chatgpt_upload_tools,
)


PUBLIC_IP = ipaddress.ip_address("93.184.216.34")
PRIVATE_IP = ipaddress.ip_address("127.0.0.1")


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}
        self.meta = {}

    def tool(self, *args, name=None, meta=None, **kwargs):
        def decorator(fn):
            tool_name = name or fn.__name__
            self.tools[tool_name] = fn
            self.meta[tool_name] = meta or {}
            return fn

        return decorator


def _registration_args(tmp_path: Path):
    exports = tmp_path / "exports"
    tmp = tmp_path / "tmp"
    exports.mkdir()
    tmp.mkdir()

    def target(filename: str):
        file_id = uuid.uuid4().hex
        return file_id, exports / f"{file_id}__{Path(filename).name}"

    def file_meta(file_id: str, path: Path, source: str, **details):
        return {
            "file_id": file_id,
            "filename": path.name.split("__", 1)[-1],
            "size_bytes": path.stat().st_size,
            "source": source,
            "details": details,
        }

    return exports, tmp, target, file_meta


def test_native_download_streams_bytes_and_enforces_public_https(tmp_path):
    payload = b"native-chatgpt-media" * 100
    destination = tmp_path / "download.part"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Length": str(len(payload)),
            },
            content=payload,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    source = {
        "download_url": "https://files.example.test/object?temporary=secret",
        "file_id": "file_native",
        "mime_type": "audio/mpeg",
        "file_name": "reference.mp3",
    }
    with patch(
        "video_mcp.chatgpt_upload._resolved_addresses",
        return_value={PUBLIC_IP},
    ):
        result = asyncio.run(
            download_openai_file_to_path(source, destination, max_bytes=1024 * 1024, client=client)
        )
    asyncio.run(client.aclose())

    assert destination.read_bytes() == payload
    assert result.size_bytes == len(payload)
    assert result.response_media_type == "audio/mpeg"
    assert result.source_host == "files.example.test"

    private_source = {"download_url": "https://127.0.0.1/file", "file_id": "file_private"}
    with patch(
        "video_mcp.chatgpt_upload._resolved_addresses",
        return_value={PRIVATE_IP},
    ):
        try:
            asyncio.run(
                download_openai_file_to_path(
                    private_source,
                    tmp_path / "private.part",
                    max_bytes=1024,
                    client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                )
            )
        except ValueError as exc:
            assert "non-public address" in str(exc)
        else:
            raise AssertionError("private-address download should have been rejected")


def test_save_uploaded_file_streams_to_cache_without_base64(tmp_path):
    exports, tmp, target, file_meta = _registration_args(tmp_path)
    mcp = FakeMCP()
    register_chatgpt_upload_tools(
        mcp,
        tmp=tmp,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
    )

    payload = b"ID3-native-audio"

    async def fake_download(source, destination, *, max_bytes, client=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return DownloadedFile(
            size_bytes=len(payload),
            sha256="a" * 64,
            response_media_type="audio/mpeg",
            source_host="files.example.test",
        )

    source = {
        "download_url": "https://files.example.test/native?sig=temporary",
        "file_id": "file_native",
        "mime_type": "audio/mpeg",
        "file_name": "voice.mp3",
    }
    with patch("video_mcp.chatgpt_upload.download_openai_file_to_path", side_effect=fake_download):
        result = asyncio.run(mcp.tools["save_uploaded_file"](source))

    assert result["filename"] == "voice.mp3"
    assert result["size_bytes"] == len(payload)
    assert result["source"] == "chatgpt-file-input"
    assert result["details"]["native_chatgpt_file_upload"] is True
    assert result["transfer_complete"] is True
    stored = next(exports.glob(f"{result['file_id']}__*"))
    assert stored.read_bytes() == payload
    assert mcp.meta["save_uploaded_file"]["openai/fileParams"] == ["file"]
    assert mcp.meta["save_uploaded_files"]["openai/fileParams"] == ["files"]


def test_real_mcp_tool_descriptors_match_openai_file_param_contract(tmp_path):
    _, tmp, target, file_meta = _registration_args(tmp_path)
    mcp = MCPServer("test-video-mcp")
    register_chatgpt_upload_tools(
        mcp,
        tmp=tmp,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
    )

    tools = asyncio.run(mcp.list_tools())
    single = next(tool for tool in tools if tool.name == "save_uploaded_file")
    batch = next(tool for tool in tools if tool.name == "save_uploaded_files")
    single_dump = single.model_dump(by_alias=True, exclude_none=True)
    batch_dump = batch.model_dump(by_alias=True, exclude_none=True)

    assert single_dump["_meta"]["openai/fileParams"] == ["file"]
    assert batch_dump["_meta"]["openai/fileParams"] == ["files"]

    schema = single_dump["inputSchema"]
    file_node = schema["properties"]["file"]
    if "$ref" in file_node:
        file_node = schema["$defs"][file_node["$ref"].split("/")[-1]]
    assert set(file_node["required"]) == {"download_url", "file_id"}
    assert set(file_node["properties"]) == {
        "download_url",
        "file_id",
        "mime_type",
        "file_name",
    }
    assert file_node.get("additionalProperties") is False
