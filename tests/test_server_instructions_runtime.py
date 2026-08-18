from __future__ import annotations

from mcp.server import MCPServer

from video_mcp.server_instructions import SERVER_INSTRUCTIONS, install_server_instructions


def test_server_instructions_cover_file_routing_and_capability_grounding():
    assert "MCP servers are independent" in SERVER_INSTRUCTIONS
    assert "NEVER put an HTTP(S) URL" in SERVER_INSTRUCTIONS
    assert "comfy_upload_cached_image(file_id)" in SERVER_INSTRUCTIONS
    assert "workflow_load_image_value" in SERVER_INSTRUCTIONS
    assert "import_remote_file(uri)" in SERVER_INSTRUCTIONS
    assert "ElevenLabs speech-to-speech is NOT implemented" in SERVER_INSTRUCTIONS
    assert "Automatic cache deletion is disabled" in SERVER_INSTRUCTIONS
    assert "cache_pin" in SERVER_INSTRUCTIONS
    assert "file_transfer_guide()" in SERVER_INSTRUCTIONS


def test_install_server_instructions_changes_initialize_instructions_field():
    mcp = MCPServer("test-video-mcp")
    assert not (mcp.instructions or "")
    install_server_instructions(mcp)
    assert mcp.instructions == SERVER_INSTRUCTIONS
