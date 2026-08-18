from __future__ import annotations

import json
import mimetypes
import time
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from video_mcp.audit import AuditLog
from video_mcp.cache_retention import CacheRetentionManager, CacheRetentionPolicy
from video_mcp.webgui import create_webgui_routes


def _fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    exports = tmp_path / "exports"
    scratch = tmp_path / "tmp"
    exports.mkdir()
    scratch.mkdir()

    def cached(file_id: str) -> Path:
        matches = list(exports.glob(f"{file_id}__*"))
        if not matches:
            raise ValueError("Cached file not found")
        return matches[0]

    def target(filename: str):
        file_id = uuid.uuid4().hex
        return file_id, exports / f"{file_id}__{Path(filename).name}"

    def file_meta(file_id: str, path: Path, source: str, **details):
        name = path.name.split("__", 1)[-1]
        meta = {
            "file_id": file_id,
            "filename": name,
            "size_bytes": path.stat().st_size,
            "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "source": source,
            "created_epoch": int(time.time()),
            "download_path": f"/files/{file_id}",
            "details": details,
        }
        (exports / f"{file_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    manager = CacheRetentionManager(exports, CacheRetentionPolicy())
    audit = AuditLog(tmp_path)
    routes = create_webgui_routes(
        exports=exports,
        tmp=scratch,
        cached=cached,
        target=target,
        file_meta=file_meta,
        retention_manager=manager,
        audit=audit,
        max_upload_mb=1,
        app_version="2.8.0",
        source_ref="v2.8.0",
    )
    return TestClient(Starlette(routes=routes)), manager, audit


def test_webgui_root_and_streamed_cache_upload(tmp_path, monkeypatch):
    client, manager, audit = _fixture(tmp_path, monkeypatch)
    root = client.get("/")
    assert root.status_code == 200
    assert "MCP Video Gen" in root.text
    assert "Activity" in root.text

    denied = client.put("/api/cache/upload?filename=clip.mp4", content=b"video")
    assert denied.status_code == 403

    uploaded = client.put(
        "/api/cache/upload?filename=clip.mp4",
        content=b"video-payload",
        headers={"X-MCP-WebGUI": "1", "Content-Type": "video/mp4"},
    )
    assert uploaded.status_code == 200
    file_id = uploaded.json()["file"]["file_id"]
    assert len(file_id) == 32
    assert manager.status()["file_count"] == 1

    listing = client.get("/api/cache").json()
    assert listing["files"][0]["filename"] == "clip.mp4"
    assert listing["files"][0]["content_type"] == "video/mp4"

    events = audit.list_events()["events"]
    assert any(event["method"] == "cache.upload" for event in events)


def test_webgui_pin_and_delete_requires_explicit_force(tmp_path, monkeypatch):
    client, _, _ = _fixture(tmp_path, monkeypatch)
    upload = client.put(
        "/api/cache/upload?filename=asset.png",
        content=b"png",
        headers={"X-MCP-WebGUI": "1", "Content-Type": "image/png"},
    ).json()
    file_id = upload["file"]["file_id"]

    assert client.post(f"/api/cache/{file_id}/pin", headers={"X-MCP-WebGUI": "1"}).status_code == 200
    blocked = client.delete(f"/api/cache/{file_id}", headers={"X-MCP-WebGUI": "1"})
    assert blocked.status_code == 409

    deleted = client.delete(
        f"/api/cache/{file_id}?force=true",
        headers={"X-MCP-WebGUI": "1"},
    )
    assert deleted.status_code == 200
    assert client.get("/api/cache").json()["files"] == []


def test_webgui_activity_endpoint_filters(tmp_path, monkeypatch):
    client, _, audit = _fixture(tmp_path, monkeypatch)
    audit.record(source="mcp", method="tools/call", tool="submit_workflow", status="success")
    audit.record(source="mcp", method="tools/call", tool="media_probe", status="error", error="boom")

    response = client.get("/api/audit?source=mcp&status=error")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["events"][0]["tool"] == "media_probe"
