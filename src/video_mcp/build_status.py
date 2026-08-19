from __future__ import annotations

import os
import platform
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any


def _package_version(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return "unknown"


def _read_source_ref() -> str:
    explicit = os.getenv("VIDEO_MCP_SOURCE_REF", "").strip()
    if explicit:
        return explicit

    app_dir = Path(os.getenv("VIDEO_MCP_APP_DIR", "/opt/video-mcp/current"))
    marker = app_dir / ".mcp-source-ready"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return value or "unknown"


def _registered_tool_names(mcp: Any) -> list[str]:
    """Return registered tool names without making a protocol round trip."""
    manager = getattr(mcp, "_tool_manager", None)
    list_tools = getattr(manager, "list_tools", None)
    if not callable(list_tools):
        return []
    try:
        tools = list_tools()
    except Exception:
        return []
    return sorted(str(tool.name) for tool in tools if getattr(tool, "name", None))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def register_build_status_tool(mcp: Any, *, server_module: Any) -> None:
    """Register a safe MCP-visible runtime/build introspection tool."""

    @mcp.tool()
    async def build_status() -> dict[str, Any]:
        """Report the MCP build/version and safe runtime configuration.

        Use this after deploys to verify the exact release seen by the connected
        MCP client. No tokens, credentials, signed-URL queries or file contents
        are returned. Dynamic third-party capabilities exposed by ComfyUI custom
        nodes must still be inspected through node introspection.
        """
        tool_names = _registered_tool_names(mcp)
        remote_allowlist = os.getenv("REMOTE_IMPORT_ALLOWED_HOSTS", "").strip()
        instructions = str(getattr(mcp, "instructions", "") or "")
        lowlevel = getattr(mcp, "_lowlevel_server", None)
        removed_upload_tools = {
            "cache_file_base64",
            "file_upload_begin",
            "file_upload_status",
            "file_upload_chunk_auto",
            "file_upload_chunk",
            "file_upload_finish",
            "file_upload_abort",
        }

        features = {
            "native_file_handoff": "get_cached_file_resource" in tool_names,
            "native_chatgpt_file_upload": all(
                name in tool_names for name in ("save_uploaded_file", "save_uploaded_files")
            ),
            "remote_file_ingress": "import_remote_file" in tool_names,
            "file_transfer_guide": "file_transfer_guide" in tool_names,
            "model_mediated_binary_upload_tools_removed": not any(
                name in tool_names for name in removed_upload_tools
            ),
            "comfy_cache_image_upload": "comfy_upload_cached_image" in tool_names,
            "comfy_cache_media_staging": "comfy_upload_cached_media" in tool_names,
            "workflow_loadimage_guard": "submit_workflow" in tool_names,
            "cache_retention_tools": all(
                name in tool_names
                for name in ("cache_status", "cache_cleanup", "cache_pin", "cache_unpin")
            ),
            "server_instructions_configured": bool(instructions.strip()),
            "server_description_configured": bool(getattr(lowlevel, "description", "") or ""),
            "webgui": _env_bool("WEBGUI_ENABLED", True),
            "persistent_activity_audit": _env_bool("AUDIT_LOG_ENABLED", True),
            "blender_bridge_tools": "blender_info" in tool_names,
            "hyperframes": "hyperframes_info" in tool_names,
            "comfyui_node_introspection": all(
                name in tool_names for name in ("list_loaded_nodes", "get_node_definition")
            ),
        }

        return {
            "server": getattr(mcp, "name", "video-mcp"),
            "server_title": str(getattr(lowlevel, "title", "") or "MCP Video Gen"),
            "app_version": os.getenv("VIDEO_MCP_APP_VERSION", "unknown"),
            "source_ref": _read_source_ref(),
            "python_version": platform.python_version(),
            "mcp_sdk_version": _package_version("mcp"),
            "tool_count": len(tool_names) if tool_names else None,
            "features": features,
            "limits": {
                "max_upload_mb": int(server_module.MAX_UPLOAD_MB),
                "max_inline_output_mb": int(server_module.MAX_INLINE_MB),
                "chatgpt_file_max_batch_files": max(1, min(_env_int("CHATGPT_FILE_MAX_BATCH_FILES", 20), 100)),
                "scan_max_files": int(server_module.SCAN_MAX_FILES),
                "ffmpeg_timeout_sec": int(server_module.FFMPEG_TIMEOUT),
                "hyperframes_timeout_sec": int(server_module.HF_TIMEOUT),
            },
            "cache_retention": {
                "cleanup_enabled": _env_bool("CACHE_CLEANUP_ENABLED", False),
                "retention_days": _env_float("CACHE_RETENTION_DAYS", 0.0),
                "max_size_gb": _env_float("CACHE_MAX_SIZE_GB", 0.0),
                "cleanup_interval_hours": _env_float("CACHE_CLEANUP_INTERVAL_HOURS", 24.0),
                "default_behavior": (
                    "persistent/no automatic deletion when CACHE_CLEANUP_ENABLED is absent or false"
                ),
            },
            "activity_audit": {
                "enabled": _env_bool("AUDIT_LOG_ENABLED", True),
                "retention_days": int(os.getenv("AUDIT_RETENTION_DAYS", "30")),
                "max_rows": int(os.getenv("AUDIT_MAX_ROWS", "20000")),
                "argument_policy": "sanitized/bounded; secret-like keys and signed URL query strings redacted",
            },
            "runtime": {
                "listen_port": int(server_module.LISTEN_PORT),
                "public_base_url_configured": bool(server_module.PUBLIC_BASE_URL),
                "cloudflare_access_verify": bool(server_module.CF_VERIFY),
                "webgui_enabled": _env_bool("WEBGUI_ENABLED", True),
                "remote_import_allowlist_configured": bool(remote_allowlist),
                "remote_import_max_redirects": int(os.getenv("REMOTE_IMPORT_MAX_REDIRECTS", "5")),
                "remote_import_timeout_sec": int(os.getenv("REMOTE_IMPORT_TIMEOUT_SEC", "120")),
                "chatgpt_file_max_redirects": max(1, min(_env_int("CHATGPT_FILE_MAX_REDIRECTS", 5), 10)),
                "chatgpt_file_timeout_sec": max(1, min(_env_int("CHATGPT_FILE_TIMEOUT_SEC", 300), 1800)),
                "blender_enabled": _env_bool("BLENDER_ENABLED", False),
                "models_mount_present": bool(server_module.MODELS.exists()),
                "custom_nodes_mount_present": bool(server_module.NODES.exists()),
            },
            "routing_rule": (
                "For a ChatGPT attachment use save_uploaded_file/save_uploaded_files: the client "
                "binds openai/fileParams and Video Gen streams the temporary authorized HTTPS "
                "download directly into its cache. For other retrievable HTTPS sources use "
                "import_remote_file. Binary base64/chunk upload tools are intentionally absent. "
                "Then reuse the canonical file_id and stage cached media into ComfyUI with "
                "comfy_upload_cached_media(file_id)."
            ),
        }
