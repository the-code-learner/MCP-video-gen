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
    """Return registered tool names without making a protocol round trip.

    MCP SDK 2.x stores tools in ToolManager. This deliberately uses defensive
    introspection so build_status still returns useful information if that
    internal surface changes in a later compatible SDK release.
    """
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


def register_build_status_tool(mcp: Any, *, server_module: Any) -> None:
    """Register a safe MCP-visible runtime/build introspection tool."""

    @mcp.tool()
    async def build_status() -> dict[str, Any]:
        """Report the MCP build/version and safe runtime configuration.

        Use this to verify that the client is talking to the expected deployed
        release after an update. It intentionally returns no tokens, secrets,
        credentials, Cloudflare audience values, signed URLs, or private file
        contents.
        """
        tool_names = _registered_tool_names(mcp)
        remote_allowlist = os.getenv("REMOTE_IMPORT_ALLOWED_HOSTS", "").strip()
        instructions = str(getattr(mcp, "instructions", "") or "")

        features = {
            "native_file_handoff": "get_cached_file_resource" in tool_names,
            "remote_file_ingress": "import_remote_file" in tool_names,
            "file_transfer_guide": "file_transfer_guide" in tool_names,
            "comfy_cache_image_upload": "comfy_upload_cached_image" in tool_names,
            "workflow_loadimage_guard": "submit_workflow" in tool_names,
            "cache_retention_tools": all(
                name in tool_names
                for name in ("cache_status", "cache_cleanup", "cache_pin", "cache_unpin")
            ),
            "server_instructions_configured": bool(instructions.strip()),
            "blender_bridge_tools": "blender_info" in tool_names,
            "hyperframes": "hyperframes_info" in tool_names,
            "elevenlabs_speech_to_speech": False,
        }

        return {
            "server": getattr(mcp, "name", "video-mcp"),
            "app_version": os.getenv("VIDEO_MCP_APP_VERSION", "unknown"),
            "source_ref": _read_source_ref(),
            "python_version": platform.python_version(),
            "mcp_sdk_version": _package_version("mcp"),
            "tool_count": len(tool_names) if tool_names else None,
            "features": features,
            "limits": {
                "max_upload_mb": int(server_module.MAX_UPLOAD_MB),
                "max_inline_output_mb": int(server_module.MAX_INLINE_MB),
                "scan_max_files": int(server_module.SCAN_MAX_FILES),
                "ffmpeg_timeout_sec": int(server_module.FFMPEG_TIMEOUT),
                "hyperframes_timeout_sec": int(server_module.HF_TIMEOUT),
            },
            "cache_retention": {
                "cleanup_enabled": _env_bool("CACHE_CLEANUP_ENABLED", False),
                "retention_days": _env_float("CACHE_RETENTION_DAYS", 0.0),
                "max_size_gb": _env_float("CACHE_MAX_SIZE_GB", 0.0),
                "cleanup_interval_hours": _env_float(
                    "CACHE_CLEANUP_INTERVAL_HOURS", 24.0
                ),
                "default_behavior": (
                    "persistent/no automatic deletion when CACHE_CLEANUP_ENABLED is absent or false"
                ),
            },
            "runtime": {
                "listen_port": int(server_module.LISTEN_PORT),
                "public_base_url_configured": bool(server_module.PUBLIC_BASE_URL),
                "cloudflare_access_verify": bool(server_module.CF_VERIFY),
                "remote_import_allowlist_configured": bool(remote_allowlist),
                "remote_import_max_redirects": int(
                    os.getenv("REMOTE_IMPORT_MAX_REDIRECTS", "5")
                ),
                "remote_import_timeout_sec": int(
                    os.getenv("REMOTE_IMPORT_TIMEOUT_SEC", "120")
                ),
                "blender_enabled": _env_bool("BLENDER_ENABLED", False),
                "models_mount_present": bool(server_module.MODELS.exists()),
                "custom_nodes_mount_present": bool(server_module.NODES.exists()),
            },
            "routing_rule": (
                "Use file_transfer_guide for file movement. Never pass external URLs, "
                "ResourceLinks, /files paths, another MCP's references, or MCP file_ids "
                "directly to standard ComfyUI LoadImage; cache/import first, then use "
                "comfy_upload_cached_image(file_id)."
            ),
        }
