from __future__ import annotations

from mcp.server import MCPServer

from video_mcp.server_instructions import (
    SERVER_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    install_server_instructions,
)


def test_server_instructions_cover_file_routing_capabilities_and_webgui():
    assert "You have access to MCP Video Gen" in SERVER_INSTRUCTIONS
    assert "MCP servers are independent" in SERVER_INSTRUCTIONS
    assert "NEVER put an HTTP(S) URL" in SERVER_INSTRUCTIONS
    assert "comfy_upload_cached_media(file_id)" in SERVER_INSTRUCTIONS
    assert "comfy_upload_cached_image" in SERVER_INSTRUCTIONS
    assert "workflow_load_image_value" in SERVER_INSTRUCTIONS
    assert "workflow_input_value" in SERVER_INSTRUCTIONS
    assert "import_remote_file(uri)" in SERVER_INSTRUCTIONS
    assert "ComfyUI custom nodes can expose third-party services" in SERVER_INSTRUCTIONS
    assert "API keys or credentials" in SERVER_INSTRUCTIONS
    assert "list_loaded_nodes" in SERVER_INSTRUCTIONS
    assert "get_node_definition" in SERVER_INSTRUCTIONS
    assert "Automatic cache deletion is disabled" in SERVER_INSTRUCTIONS
    assert "cache_pin" in SERVER_INSTRUCTIONS
    assert "WebGUI at `/`" in SERVER_INSTRUCTIONS
    assert "file_transfer_guide()" in SERVER_INSTRUCTIONS
    assert "media-generation" in SERVER_DESCRIPTION
    assert "ComfyUI" in SERVER_DESCRIPTION


def test_install_server_instructions_sets_initialize_identity_fields(monkeypatch):
    monkeypatch.setenv("VIDEO_MCP_APP_VERSION", "2.8.0")
    mcp = MCPServer("test-video-mcp")
    assert not (mcp.instructions or "")
    install_server_instructions(mcp)
    assert mcp.instructions == SERVER_INSTRUCTIONS
    lowlevel = mcp._lowlevel_server
    assert lowlevel.title == "MCP Video Gen"
    assert lowlevel.description == SERVER_DESCRIPTION
    assert lowlevel.version == "2.8.0"
