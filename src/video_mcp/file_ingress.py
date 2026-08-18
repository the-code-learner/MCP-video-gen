from __future__ import annotations

import asyncio
import functools
import hashlib
import ipaddress
import mimetypes
import os
import re
import shutil
import socket
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx


_REDIRECT_CODES = {301, 302, 303, 307, 308}
_FILE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_URL_PREFIXES = ("http://", "https://", "file://", "media://", "data:")


def _allowed_host_patterns() -> tuple[str, ...]:
    raw = os.getenv("REMOTE_IMPORT_ALLOWED_HOSTS", "")
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _host_matches_allowlist(host: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    host = host.lower().rstrip(".")
    for pattern in patterns:
        pattern = pattern.lower().rstrip(".")
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == pattern:
            return True
    return False


async def _validate_remote_url(uri: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "https":
        raise ValueError("Remote import requires an HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Remote import URLs must not contain embedded credentials")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        raise ValueError("Remote import URL has no hostname")
    if not _host_matches_allowlist(host, allowed_hosts):
        raise ValueError(f"Remote host is not allowed by REMOTE_IMPORT_ALLOWED_HOSTS: {host}")

    port = parsed.port or 443
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError(f"Could not resolve remote host {host}: {exc}") from exc

    addresses = {info[4][0] for info in infos if info and info[4]}
    if not addresses:
        raise ValueError(f"Could not resolve remote host {host}")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(f"Remote host resolved to an invalid address: {value}") from exc
        if not address.is_global:
            raise ValueError(
                f"Remote import refuses non-public address {address} for host {host}"
            )
    return host


def _sanitize_filename(value: str) -> str:
    name = Path(unquote(value)).name.strip()
    name = name.replace("\x00", "")
    if not name or name in {".", ".."}:
        return ""
    return name[:255]


def _response_filename(response: httpx.Response, final_url: str, explicit: str) -> str:
    if explicit:
        name = _sanitize_filename(explicit)
        if not name:
            raise ValueError("filename is invalid")
        return name

    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, flags=re.IGNORECASE)
    if match:
        name = _sanitize_filename(match.group(1))
        if name:
            return name

    path_name = _sanitize_filename(urlparse(final_url).path)
    if path_name:
        name = path_name
    else:
        name = "remote-file"

    if not Path(name).suffix:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        extension = mimetypes.guess_extension(content_type) if content_type else None
        if extension:
            name += extension
        else:
            name += ".bin"
    return name


def _validate_expected_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if normalized and not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
    return normalized


def _invalid_load_image_inputs(workflow: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") != "LoadImage":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        value = inputs.get("image")
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        lowered = stripped.lower()
        reason = ""
        if lowered.startswith(_URL_PREFIXES) or lowered.startswith("/files/"):
            reason = "URL/resource references are not valid standard ComfyUI LoadImage inputs"
        elif _FILE_ID_RE.fullmatch(stripped):
            reason = "an MCP file_id is not a ComfyUI input filename"
        if reason:
            issues.append({"node_id": str(node_id), "value": stripped[:300], "reason": reason})
    return issues


def install_comfy_workflow_input_guard(server_module: Any) -> None:
    """Guard standard LoadImage nodes against URLs and MCP file IDs.

    AI clients sometimes confuse an MCP cache URL/file_id with a ComfyUI input
    filename. Standard LoadImage cannot consume those references. The correct
    route is cache file_id -> comfy_upload_cached_image -> returned ComfyUI
    filename -> LoadImage.
    """

    original = server_module.submit_workflow

    @functools.wraps(original)
    async def guarded_submit_workflow(workflow: dict[str, Any], client_id: str = "") -> Any:
        issues = _invalid_load_image_inputs(workflow)
        if issues:
            first = issues[0]
            raise ValueError(
                "Invalid standard ComfyUI LoadImage input at node "
                f"{first['node_id']}: {first['reason']}. Never pass an HTTP(S) URL, "
                "MCP resource URI, /files path, or MCP file_id directly to LoadImage. "
                "If the file is not in the MCP cache, import it first with import_remote_file "
                "when a server-retrievable HTTPS URL is available, otherwise use the supported "
                "client upload fallback. Then call comfy_upload_cached_image(file_id) and use "
                "the returned workflow_load_image_value in LoadImage.inputs.image."
            )
        return await original(workflow, client_id)

    server_module.submit_workflow = guarded_submit_workflow


def register_file_ingress_tools(
    mcp: Any,
    *,
    tmp: Path,
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    max_upload_mb: int,
) -> None:
    """Register reference-first client -> cache ingress and routing guidance."""

    remote_root = (tmp / "remote-imports").resolve()
    allowed_hosts = _allowed_host_patterns()
    max_redirects = max(0, min(10, int(os.getenv("REMOTE_IMPORT_MAX_REDIRECTS", "5"))))
    timeout_seconds = max(10, min(1800, int(os.getenv("REMOTE_IMPORT_TIMEOUT_SEC", "120"))))

    @mcp.tool()
    async def import_remote_file(
        uri: str,
        filename: str = "",
        expected_size_bytes: int = 0,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        """Import a server-retrievable HTTPS file reference into the MCP cache.

        Preferred client -> cache path when the client supplies a real HTTPS URL
        that this MCP server can fetch. The file is streamed directly to disk;
        its bytes do not pass through the LLM/tool JSON as base64. HTTPS only,
        public-address validation, redirect re-validation, byte limits and
        optional SHA-256/size checks are enforced.

        IMPORTANT: do not pass `uri` directly to ComfyUI LoadImage. For an image,
        first import it here, then call `comfy_upload_cached_image(file_id)`, then
        use that tool's `workflow_load_image_value` in the ComfyUI workflow.
        Opaque ChatGPT/OpenAI file IDs are not URLs and cannot be dereferenced by
        this tool unless the client exposes a server-retrievable HTTPS reference.
        """
        if expected_size_bytes < 0:
            raise ValueError("expected_size_bytes must be >= 0")
        expected_sha = _validate_expected_sha256(expected_sha256)
        limit = max_upload_mb * 1024 * 1024
        if expected_size_bytes and expected_size_bytes > limit:
            raise ValueError("Expected remote file exceeds MAX_UPLOAD_MB")

        remote_root.mkdir(parents=True, exist_ok=True)
        part = remote_root / f"{uuid.uuid4().hex}.part"
        current_url = uri.strip()
        if not current_url:
            raise ValueError("uri is required")

        digest = hashlib.sha256()
        total = 0
        final_host = ""
        response_content_type = ""
        selected_filename = ""

        timeout = httpx.Timeout(timeout_seconds, connect=min(20, timeout_seconds))
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                for redirect_index in range(max_redirects + 1):
                    final_host = await _validate_remote_url(current_url, allowed_hosts)
                    async with client.stream(
                        "GET",
                        current_url,
                        headers={
                            "Accept": "*/*",
                            "User-Agent": "mcp-video-gen-remote-import",
                        },
                    ) as response:
                        if response.status_code in _REDIRECT_CODES:
                            location = response.headers.get("location", "")
                            if not location:
                                raise ValueError("Remote server returned a redirect without Location")
                            if redirect_index >= max_redirects:
                                raise ValueError("Remote import exceeded redirect limit")
                            current_url = urljoin(current_url, location)
                            continue

                        response.raise_for_status()
                        content_length = response.headers.get("content-length", "")
                        if content_length.isdigit() and int(content_length) > limit:
                            raise ValueError("Remote file exceeds MAX_UPLOAD_MB")
                        if (
                            expected_size_bytes
                            and content_length.isdigit()
                            and int(content_length) != expected_size_bytes
                        ):
                            raise ValueError(
                                "Remote Content-Length does not match expected_size_bytes"
                            )

                        selected_filename = _response_filename(response, current_url, filename)
                        response_content_type = response.headers.get("content-type", "").split(";", 1)[0]
                        with part.open("wb") as handle:
                            async for chunk in response.aiter_bytes():
                                if not chunk:
                                    continue
                                total += len(chunk)
                                if total > limit:
                                    raise ValueError("Remote file exceeds MAX_UPLOAD_MB while streaming")
                                handle.write(chunk)
                                digest.update(chunk)
                        break
                else:  # pragma: no cover
                    raise ValueError("Remote import exceeded redirect limit")

            if expected_size_bytes and total != expected_size_bytes:
                raise ValueError(
                    f"Remote file size mismatch: expected {expected_size_bytes}, got {total}"
                )
            actual_sha = digest.hexdigest()
            if expected_sha and actual_sha != expected_sha:
                raise ValueError(
                    f"SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
                )

            file_id, out = target(selected_filename or "remote-file.bin")
            shutil.move(str(part), str(out))
            return file_meta(
                file_id,
                out,
                "remote-import",
                sha256=actual_sha,
                remote_import=True,
                source_host=final_host,
                response_content_type=response_content_type,
            )
        except Exception:
            part.unlink(missing_ok=True)
            raise

    @mcp.tool()
    async def file_transfer_guide() -> dict[str, Any]:
        """Return the canonical routing rules for moving files through MCP Video Gen.

        Call/read this when deciding how to move an attachment, generated output,
        image, video, audio, .blend or GLB. The critical rule is that ComfyUI's
        standard LoadImage expects a ComfyUI input filename, never an HTTP URL,
        MCP ResourceLink, /files path or MCP cache file_id.
        """
        return {
            "canonical_identifier": "file_id inside the MCP cache",
            "golden_rules": [
                "Never pass HTTP(S) URLs, ResourceLinks, /files paths, or MCP file_ids directly to ComfyUI standard LoadImage.",
                "For an image already in the MCP cache: comfy_upload_cached_image(file_id), then use workflow_load_image_value in LoadImage.inputs.image.",
                "For a client file with a server-retrievable HTTPS URL: import_remote_file(uri) -> file_id. Do not send the URL to ComfyUI.",
                "If the client only exposes opaque local/OpenAI file IDs and no retrievable URL, use the supported client upload fallback; do not invent a URL.",
                "Prefer native references/streaming over base64. Base64 upload/read tools are compatibility fallbacks.",
                "Never resize, recompress or transcode only to make a file fit through a tool result.",
            ],
            "routes": {
                "client_reference_to_cache": {
                    "preferred": "import_remote_file(uri)",
                    "requires": "server-retrievable public HTTPS URL",
                    "result": "file_id",
                    "fallback": "cache_file_base64 or file_upload_begin/chunk/finish",
                },
                "cache_image_to_comfyui_input": {
                    "tool": "comfy_upload_cached_image(file_id)",
                    "transport": "server-side multipart/form-data; no base64",
                    "then": "use workflow_load_image_value in standard LoadImage.inputs.image",
                },
                "comfyui_output_to_cache": {
                    "tool": "cache_output(filename, subfolder, output_type)",
                    "result": "file_id",
                },
                "cache_to_client": {
                    "preferred": "get_cached_file_resource(file_id)",
                    "large_file_path": "HTTPS /files/{file_id} streaming when PUBLIC_BASE_URL is configured",
                    "fallback": "resources/read, then chunked/inline base64 only for compatibility",
                },
                "cache_to_blender": {
                    "method": "pass file_id directly to Blender MCP tools/input_files; the bridge receives binary bytes server-side",
                },
                "blender_to_cache": {
                    "method": "Blender MCP tools automatically cache declared outputs and return file_ids",
                },
                "cache_to_hyperframes": {
                    "tool": "hyperframes_import_cached_media(project_id, file_id, destination)",
                },
                "cache_audio_or_video_to_comfyui": {
                    "status": "no generic standard adapter currently",
                    "rule": "do not pass a URL to an unrelated ComfyUI node; use an installed node/API that explicitly accepts that media type, or add a dedicated adapter",
                },
            },
        }
