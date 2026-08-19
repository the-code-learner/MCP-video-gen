from __future__ import annotations

import os

import uvicorn
from starlette.routing import Mount

from . import server
from .advanced_tools import register_advanced_tools
from .audit import AuditLog, install_mcp_audit_middleware
from .build_status import register_build_status_tool
from .cache_adapters import register_cache_adapters
from .cache_retention import register_cache_retention_tools
from .chatgpt_upload import register_chatgpt_upload_tools
from .external_backends import register_external_backend_tools
from .file_ingress import install_comfy_workflow_input_guard, register_file_ingress_tools
from .file_transfer import register_file_transfer_tools
from .native_handoff import register_native_file_handoff
from .routing_guide import replace_file_transfer_guide
from .server_instructions import install_server_instructions
from .webgui import create_webgui_routes, webgui_enabled

# Publish global MCP identity/usage guidance before the server accepts clients.
install_server_instructions(server.mcp)

# The audit store is lazy: importing this module in CI does not create /data.
audit_log = AuditLog(server.DATA_ROOT)
install_mcp_audit_middleware(server.mcp, audit_log)

register_file_transfer_tools(
    server.mcp,
    exports=server.EXPORTS,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
    max_upload_mb=server.MAX_UPLOAD_MB,
    max_inline_mb=server.MAX_INLINE_MB,
)

register_native_file_handoff(
    server.mcp,
    cached=server.cached,
    public_base_url=server.PUBLIC_BASE_URL,
)

# ChatGPT-native attachment ingress. The client binds attached files to the
# openai/fileParams fields and supplies temporary authorized download objects;
# the MCP streams those bytes directly to its persistent cache.
register_chatgpt_upload_tools(
    server.mcp,
    tmp=server.TMP,
    target=server.target,
    file_meta=server.file_meta,
    max_upload_mb=server.MAX_UPLOAD_MB,
)

register_file_ingress_tools(
    server.mcp,
    tmp=server.TMP,
    target=server.target,
    file_meta=server.file_meta,
    max_upload_mb=server.MAX_UPLOAD_MB,
)

register_advanced_tools(
    server.mcp,
    data_root=server.DATA_ROOT,
    exports=server.EXPORTS,
    tmp=server.TMP,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
    command=server.command,
    ffmpeg_timeout=server.FFMPEG_TIMEOUT,
)

# Standard ComfyUI LoadImage accepts a ComfyUI input filename, not an MCP
# file_id or URL. Install this before external-backend wrappers capture the
# submit_workflow implementation so AI-generated workflows get a clear routing
# error instead of sending unusable links to ComfyUI.
install_comfy_workflow_input_guard(server)

register_external_backend_tools(
    server.mcp,
    server_module=server,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
)

register_cache_adapters(
    server.mcp,
    server_module=server,
    cached=server.cached,
)

# Replace the older guide now that generic cache->ComfyUI staging and native
# ChatGPT file-param ingress are available.
replace_file_transfer_guide(server.mcp)

# Retention is fully opt-in. Register the tools unconditionally so clients can
# inspect policy/status, but start the automatic worker only when explicitly
# enabled after privileges have been dropped.
cache_retention_manager = register_cache_retention_tools(
    server.mcp,
    exports=server.EXPORTS,
)

# The Starlette shell already mounts MCP last at '/'. Insert WebGUI/API routes
# immediately before that catch-all mount so `/mcp` remains owned by MCP while
# `/`, `/api/*` and `/files/*` stay ordinary HTTP routes behind the same auth.
if webgui_enabled():
    routes = create_webgui_routes(
        exports=server.EXPORTS,
        tmp=server.TMP,
        cached=server.cached,
        target=server.target,
        file_meta=server.file_meta,
        retention_manager=cache_retention_manager,
        audit=audit_log,
        max_upload_mb=server.MAX_UPLOAD_MB,
        app_version=os.getenv("VIDEO_MCP_APP_VERSION", "unknown"),
        source_ref=os.getenv("VIDEO_MCP_SOURCE_REF", "unknown"),
    )
    starlette_app = server.app.app
    mount_index = next(
        (index for index, route in enumerate(starlette_app.router.routes) if isinstance(route, Mount)),
        len(starlette_app.router.routes),
    )
    for route in routes:
        starlette_app.router.routes.insert(mount_index, route)
        mount_index += 1

# Register last so build_status sees the complete runtime tool set, including
# itself, and can report the same count a connected MCP client should discover.
register_build_status_tool(server.mcp, server_module=server)


if __name__ == "__main__":
    server.drop_privileges()
    cache_retention_manager.start_worker()
    uvicorn.run(
        server.app,
        host="0.0.0.0",
        port=server.LISTEN_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
