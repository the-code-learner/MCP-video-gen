from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from pathlib import Path

from video_mcp.file_transfer import MAX_CHUNK_DECODED_BYTES, register_file_transfer_tools
from video_mcp.routing_guide import replace_file_transfer_guide
from video_mcp.server_instructions import SERVER_INSTRUCTIONS


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
    tmp = tmp_path / "tmp"
    exports.mkdir()
    tmp.mkdir()

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
        tmp=tmp,
        cached=cached,
        target=target,
        file_meta=file_meta,
        max_upload_mb=32,
        max_inline_mb=8,
    )
    return mcp


def test_begin_exposes_programmatic_upload_contract(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    payload = b"a" * 89_458

    begun = asyncio.run(
        mcp.tools["file_upload_begin"](
            "speech.wav",
            expected_size_bytes=len(payload),
        )
    )
    assert begun["max_chunk_decoded_bytes"] == MAX_CHUNK_DECODED_BYTES
    assert begun["recommended_next_chunk_bytes"] == len(payload)
    assert begun["preferred_chunk_tool"] == "file_upload_chunk_auto"
    assert begun["programmatic_tool_calling"]["recommended_when_supported"] is True
    assert begun["programmatic_tool_calling"]["no_model_turn_between_chunks"] is True
    assert begun["programmatic_tool_calling"]["status_tool"] == "file_upload_status"


def test_auto_chunk_rejects_decoded_size_mismatch_without_advancing(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    payload = b"0123456789" * 100
    begun = asyncio.run(
        mcp.tools["file_upload_begin"](
            "speech.mp3",
            expected_size_bytes=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    )

    rejected = asyncio.run(
        mcp.tools["file_upload_chunk_auto"](
            begun["upload_id"],
            base64.b64encode(payload[:300]).decode("ascii"),
            expected_decoded_bytes=299,
        )
    )
    assert rejected["accepted"] is False
    assert rejected["file_unchanged"] is True
    assert rejected["reason"] == "decoded_size_mismatch"
    assert rejected["decoded_bytes"] == 300
    assert rejected["total_received_bytes"] == 0

    status = asyncio.run(mcp.tools["file_upload_status"](begun["upload_id"]))
    assert status["total_received_bytes"] == 0
    assert status["remaining_bytes"] == len(payload)


def test_auto_chunk_owns_offset_and_finishes_verified_upload(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    payload = bytes(range(256)) * 100
    digest = hashlib.sha256(payload).hexdigest()
    begun = asyncio.run(
        mcp.tools["file_upload_begin"](
            "reference.bin",
            expected_size_bytes=len(payload),
            expected_sha256=digest,
        )
    )

    cut = 12_000
    first = payload[:cut]
    second = payload[cut:]

    r1 = asyncio.run(
        mcp.tools["file_upload_chunk_auto"](
            begun["upload_id"],
            base64.b64encode(first).decode("ascii"),
            expected_decoded_bytes=len(first),
        )
    )
    assert r1["accepted"] is True
    assert r1["received_bytes"] == len(first)
    assert r1["total_received_bytes"] == len(first)
    assert r1["complete_by_size"] is False

    r2 = asyncio.run(
        mcp.tools["file_upload_chunk_auto"](
            begun["upload_id"],
            base64.b64encode(second).decode("ascii"),
            expected_decoded_bytes=len(second),
        )
    )
    assert r2["accepted"] is True
    assert r2["total_received_bytes"] == len(payload)
    assert r2["remaining_bytes"] == 0
    assert r2["complete_by_size"] is True
    assert r2["next_action"] == "file_upload_finish(upload_id)"

    finished = asyncio.run(mcp.tools["file_upload_finish"](begun["upload_id"]))
    assert finished["size_bytes"] == len(payload)
    assert finished["details"]["sha256"] == digest
    assert finished["transfer_complete"] is True
    assert finished["do_not_reencode_or_reupload"] is True


def test_auto_chunk_rejects_overrun_atomically(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    payload = b"a" * 100
    begun = asyncio.run(mcp.tools["file_upload_begin"]("tiny.bin", expected_size_bytes=100))

    accepted = asyncio.run(
        mcp.tools["file_upload_chunk_auto"](
            begun["upload_id"],
            base64.b64encode(payload[:90]).decode("ascii"),
            expected_decoded_bytes=90,
        )
    )
    assert accepted["total_received_bytes"] == 90

    rejected = asyncio.run(
        mcp.tools["file_upload_chunk_auto"](
            begun["upload_id"],
            base64.b64encode(payload[:20]).decode("ascii"),
            expected_decoded_bytes=20,
        )
    )
    assert rejected["accepted"] is False
    assert rejected["reason"] == "chunk_would_exceed_expected_size"
    assert rejected["file_unchanged"] is True
    assert rejected["total_received_bytes"] == 90

    status = asyncio.run(mcp.tools["file_upload_status"](begun["upload_id"]))
    assert status["total_received_bytes"] == 90
    assert status["remaining_bytes"] == 10


def test_legacy_explicit_offset_chunk_remains_compatible(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    payload = b"a" * 89_458
    begun = asyncio.run(mcp.tools["file_upload_begin"]("speech.wav", expected_size_bytes=len(payload)))
    uploaded = asyncio.run(
        mcp.tools["file_upload_chunk"](
            begun["upload_id"],
            0,
            base64.b64encode(payload).decode("ascii"),
        )
    )
    assert uploaded["remaining_bytes"] == 0
    assert uploaded["next_action"] == "file_upload_finish(upload_id)"
    assert uploaded["recommended_next_chunk_bytes"] == 0


def test_tool_docstrings_explain_ptc_and_server_owned_offset(tmp_path):
    mcp = _register_transfer_tools(tmp_path)
    one_shot = mcp.tools["cache_file_base64"].__doc__ or ""
    begin = mcp.tools["file_upload_begin"].__doc__ or ""
    auto = mcp.tools["file_upload_chunk_auto"].__doc__ or ""
    legacy = mcp.tools["file_upload_chunk"].__doc__ or ""

    assert "single call" in one_shot
    assert "Programmatic Tool Calling" in one_shot
    assert "Programmatic Tool Calling" in begin
    assert "SERVER owns the offset" in auto
    assert "accepted=false" in auto
    assert "Prefer `file_upload_chunk_auto`" in legacy


def test_routing_guide_contains_ptc_decision_tree():
    mcp = FakeMCP()
    mcp.tools["file_transfer_guide"] = lambda: None
    replace_file_transfer_guide(mcp)

    guide = asyncio.run(mcp.tools["file_transfer_guide"]())
    assert guide["client_to_cache_decision"][0]["action"] == "import_remote_file(uri)"
    assert "cache_file_base64" in guide["client_to_cache_decision"][1]["action"]
    assert "PTC" in guide["client_to_cache_decision"][2]["action"]
    assert guide["chunking"]["max_decoded_bytes_per_call"] == 4 * 1024 * 1024
    assert guide["chunking"]["preferred_tool"] == "file_upload_chunk_auto"
    assert guide["chunking"]["server_owned_offset"] is True
    assert guide["programmatic_tool_calling"]["server_can_enable_ptc"] is False
    assert guide["programmatic_tool_calling"]["client_must_support_ptc"] is True


def test_server_instructions_define_ptc_upload_stage():
    assert "Programmatic Tool Calling (PTC) is a CLIENT/API capability" in SERVER_INSTRUCTIONS
    assert "file_upload_chunk_auto" in SERVER_INSTRUCTIONS
    assert "Do not return to the language model between successful chunks" in SERVER_INSTRUCTIONS
