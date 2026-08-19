from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_entrypoint_registers_optional_backends_and_file_transfer():
    source = (ROOT / "src" / "video_mcp" / "entrypoint.py").read_text()
    assert "register_external_backend_tools" in source
    assert "register_file_transfer_tools" in source
    assert "register_chatgpt_upload_tools" in source


def test_external_tools_include_soft_availability_and_blender_primitives():
    source = (ROOT / "src" / "video_mcp" / "external_backends.py").read_text()
    for name in (
        "external_backends_status",
        "blender_info",
        "blender_execute_python",
        "blender_render_blend",
        "blender_render_animation",
        "blender_export_glb",
    ):
        assert f"def {name}(" in source
    assert '"available": False' in source
    assert '"status": "unavailable"' in source
    assert "mcp.remove_tool" in source
    assert '"cache_output"' in source
    assert '"submit_workflow"' in source


def test_file_transport_uses_native_chatgpt_ingress_and_reference_first_handoff():
    native = (ROOT / "src" / "video_mcp" / "chatgpt_upload.py").read_text()
    assert "class OpenAIFile" in native
    assert '"openai/fileParams": ["file"]' in native
    assert '"openai/fileParams": ["files"]' in native
    assert "def save_uploaded_file(" in native
    assert "def save_uploaded_files(" in native
    assert "download_openai_file_to_path" in native
    assert "hashlib.sha256" in native

    transfer = (ROOT / "src" / "video_mcp" / "file_transfer.py").read_text()
    for name in (
        "cache_text_file",
        "get_cached_file_info",
        "read_cached_file_chunk_base64",
    ):
        assert f"def {name}(" in transfer
    for removed in (
        "cache_file_base64",
        "file_upload_begin",
        "file_upload_status",
        "file_upload_chunk_auto",
        "file_upload_chunk",
        "file_upload_finish",
        "file_upload_abort",
    ):
        assert f"def {removed}(" not in transfer

    ingress = (ROOT / "src" / "video_mcp" / "file_ingress.py").read_text()
    assert "def import_remote_file(" in ingress
    server = (ROOT / "src" / "video_mcp" / "server.py").read_text()
    assert 'Route("/files/{file_id}"' in server


def test_blender_bridge_is_authenticated_headless_and_path_scoped():
    bridge = (ROOT / "scripts" / "blender_bridge.py").read_text()
    assert "BLENDER_BRIDGE_TOKEN must be set" in bridge
    assert "hmac.compare_digest" in bridge
    assert '"--background"' in bridge
    assert '"--factory-startup"' in bridge
    assert '"--disable-autoexec"' in bridge
    assert '"--python-exit-code"' in bridge
    assert "BLENDER_INPUT_DIR" in bridge
    assert "BLENDER_OUTPUT_DIR" in bridge
    assert "expected_outputs" in bridge
    # /v1/jobs/<id>/outputs/<filename> has five path components; nested
    # outputs have more. This regression guard prevents making all top-level
    # output downloads unreachable.
    assert "len(parts) >= 5" in bridge


def test_stack_keeps_external_backends_optional():
    stack = (ROOT / "video-mcp.yml").read_text()
    assert 'BLENDER_ENABLED: "${BLENDER_ENABLED:-false}"' in stack
    assert "BLENDER_BRIDGE_URL" in stack
    assert "BLENDER_BRIDGE_TOKEN" in stack
    assert "/var/lib/mcp-video-gen/empty/comfy-models" in stack
    assert "/var/lib/mcp-video-gen/empty/comfy-custom-nodes" in stack
