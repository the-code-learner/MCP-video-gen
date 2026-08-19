from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from video_mcp.build_status import register_build_status_tool


class FakeToolManager:
    def __init__(self, owner):
        self.owner = owner

    def list_tools(self):
        return [SimpleNamespace(name=name) for name in self.owner.tools]


class FakeMCP:
    def __init__(self):
        self.name = "video-mcp"
        self.tools = {
            "get_cached_file_resource": object(),
            "save_uploaded_file": object(),
            "save_uploaded_files": object(),
            "import_remote_file": object(),
            "file_transfer_guide": object(),
            "comfy_upload_cached_image": object(),
            "comfy_upload_cached_media": object(),
            "submit_workflow": object(),
            "blender_info": object(),
            "hyperframes_info": object(),
            "list_loaded_nodes": object(),
            "get_node_definition": object(),
        }
        self._tool_manager = FakeToolManager(self)

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def fake_server(tmp_path: Path):
    models = tmp_path / "models"
    nodes = tmp_path / "nodes"
    models.mkdir()
    nodes.mkdir()
    return SimpleNamespace(
        MAX_UPLOAD_MB=32,
        MAX_INLINE_MB=8,
        SCAN_MAX_FILES=5000,
        FFMPEG_TIMEOUT=1800,
        HF_TIMEOUT=3600,
        LISTEN_PORT=8000,
        PUBLIC_BASE_URL="https://example.invalid",
        CF_VERIFY=True,
        MODELS=models,
        NODES=nodes,
    )


def test_build_status_reports_deployed_identity_and_safe_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_MCP_APP_VERSION", "3.0.0")
    monkeypatch.setenv("VIDEO_MCP_SOURCE_REF", "v3.0.0")
    monkeypatch.setenv("REMOTE_IMPORT_ALLOWED_HOSTS", "files.example.invalid")
    monkeypatch.setenv("REMOTE_IMPORT_MAX_REDIRECTS", "4")
    monkeypatch.setenv("REMOTE_IMPORT_TIMEOUT_SEC", "90")
    monkeypatch.setenv("CHATGPT_FILE_MAX_BATCH_FILES", "12")
    monkeypatch.setenv("CHATGPT_FILE_MAX_REDIRECTS", "3")
    monkeypatch.setenv("CHATGPT_FILE_TIMEOUT_SEC", "240")
    monkeypatch.setenv("BLENDER_ENABLED", "true")

    # Secrets may exist in the process environment but must never be surfaced.
    monkeypatch.setenv("BLENDER_BRIDGE_TOKEN", "do-not-leak-this-token")
    monkeypatch.setenv("CF_ACCESS_AUD", "do-not-leak-this-audience")

    mcp = FakeMCP()
    register_build_status_tool(mcp, server_module=fake_server(tmp_path))

    result = asyncio.run(mcp.tools["build_status"]())
    assert result["server"] == "video-mcp"
    assert result["app_version"] == "3.0.0"
    assert result["source_ref"] == "v3.0.0"
    assert result["tool_count"] == len(mcp.tools)
    assert result["features"]["native_file_handoff"] is True
    assert result["features"]["native_chatgpt_file_upload"] is True
    assert result["features"]["remote_file_ingress"] is True
    assert result["features"]["file_transfer_guide"] is True
    assert result["features"]["model_mediated_binary_upload_tools_removed"] is True
    assert result["features"]["comfy_cache_image_upload"] is True
    assert result["features"]["comfy_cache_media_staging"] is True
    assert result["limits"]["max_upload_mb"] == 32
    assert result["limits"]["chatgpt_file_max_batch_files"] == 12
    assert result["runtime"]["listen_port"] == 8000
    assert result["runtime"]["public_base_url_configured"] is True
    assert result["runtime"]["cloudflare_access_verify"] is True
    assert result["runtime"]["remote_import_allowlist_configured"] is True
    assert result["runtime"]["remote_import_max_redirects"] == 4
    assert result["runtime"]["remote_import_timeout_sec"] == 90
    assert result["runtime"]["chatgpt_file_max_redirects"] == 3
    assert result["runtime"]["chatgpt_file_timeout_sec"] == 240
    assert result["runtime"]["blender_enabled"] is True
    assert result["runtime"]["models_mount_present"] is True
    assert result["runtime"]["custom_nodes_mount_present"] is True
    assert "save_uploaded_file" in result["routing_rule"]
    assert "Binary base64/chunk upload tools are intentionally absent" in result["routing_rule"]

    serialized = json.dumps(result)
    assert "do-not-leak-this-token" not in serialized
    assert "do-not-leak-this-audience" not in serialized


def test_build_status_reads_bootstrap_source_marker_when_env_is_absent(tmp_path, monkeypatch):
    app_dir = tmp_path / "current"
    app_dir.mkdir()
    (app_dir / ".mcp-source-ready").write_text("v3.0.0\n", encoding="utf-8")

    monkeypatch.delenv("VIDEO_MCP_SOURCE_REF", raising=False)
    monkeypatch.setenv("VIDEO_MCP_APP_DIR", str(app_dir))
    monkeypatch.setenv("VIDEO_MCP_APP_VERSION", "3.0.0")

    mcp = FakeMCP()
    register_build_status_tool(mcp, server_module=fake_server(tmp_path))
    result = asyncio.run(mcp.tools["build_status"]())

    assert result["source_ref"] == "v3.0.0"
