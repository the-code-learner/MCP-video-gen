from __future__ import annotations

import asyncio

from video_mcp.audit import AuditLog, install_mcp_audit_middleware


class FakeMCP:
    def __init__(self):
        self.middleware = []


class FakeContext:
    method = "tools/call"
    params = {
        "name": "demo_tool",
        "arguments": {
            "file_id": "a" * 32,
            "api_key": "must-not-leak",
            "uri": "https://example.invalid/file.bin?signature=must-not-leak",
            "payload": "A" * 500,
        },
    }


def test_audit_redacts_secrets_and_signed_url_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "7")
    monkeypatch.setenv("AUDIT_MAX_ROWS", "1000")
    audit = AuditLog(tmp_path)
    audit.record(
        source="webgui",
        method="cache.upload",
        status="success",
        arguments={
            "api_key": "super-secret",
            "uri": "https://files.example.invalid/x.mp4?token=hidden",
        },
        result={"file_id": "b" * 32},
    )

    result = audit.list_events()
    assert result["enabled"] is True
    assert result["count"] == 1
    event = result["events"][0]
    assert event["arguments"]["api_key"] == "<redacted>"
    assert event["arguments"]["uri"] == "https://files.example.invalid/x.mp4"
    assert "super-secret" not in str(result)
    assert "token=hidden" not in str(result)


def test_mcp_middleware_records_tool_name_status_and_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    audit = AuditLog(tmp_path)
    mcp = FakeMCP()
    install_mcp_audit_middleware(mcp, audit)
    assert len(mcp.middleware) == 1

    async def call_next(_ctx):
        return {"ok": True}

    result = asyncio.run(mcp.middleware[0](FakeContext(), call_next))
    assert result == {"ok": True}
    rows = audit.list_events()["events"]
    assert len(rows) == 1
    assert rows[0]["source"] == "mcp"
    assert rows[0]["method"] == "tools/call"
    assert rows[0]["tool"] == "demo_tool"
    assert rows[0]["status"] == "success"
    assert rows[0]["arguments"]["api_key"] == "<redacted>"
    assert rows[0]["duration_ms"] is not None


def test_audit_can_be_disabled_without_creating_database(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "false")
    audit = AuditLog(tmp_path)
    audit.record(source="mcp", method="tools/list", status="success")
    assert audit.list_events() == {"enabled": False, "events": [], "count": 0}
    assert not audit.path.exists()
