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
from .local_ai_v31 import register_local_ai_tools
from .native_handoff import register_native_file_handoff
from .piper_catalog import register_piper_catalog_tools
from .routing_guide import replace_file_transfer_guide
from .server_instructions import install_server_instructions
from .webgui import create_webgui_routes, webgui_enabled
from .webgui_system import create_system_webgui_routes

install_server_instructions(server.mcp)

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

# Qwen dependencies/models stay isolated under /data and the child worker is
# created only when inference/metrics actually need it.
qwen_worker = register_local_ai_tools(
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
register_piper_catalog_tools(server.mcp)

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

replace_file_transfer_guide(server.mcp)

cache_retention_manager = register_cache_retention_tools(
    server.mcp,
    exports=server.EXPORTS,
)

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
    routes.extend(
        create_system_webgui_routes(
            data_root=server.DATA_ROOT,
            worker_metrics=qwen_worker.metrics,
            runtime_installed=qwen_worker.runtime_installed,
        )
    )
    starlette_app = server.app.app
    mount_index = next(
        (index for index, route in enumerate(starlette_app.router.routes) if isinstance(route, Mount)),
        len(starlette_app.router.routes),
    )
    for route in routes:
        starlette_app.router.routes.insert(mount_index, route)
        mount_index += 1

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
