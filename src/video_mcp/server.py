from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jwt
import uvicorn
from jwt import PyJWKClient
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


COMFY_SCHEME = os.getenv("COMFYUI_SCHEME", "http")
COMFY_HOST = os.getenv("COMFYUI_HOST", "host.docker.internal")
COMFY_PORT = int(os.getenv("COMFYUI_PORT", "8188"))
COMFY_PATH = os.getenv("COMFYUI_BASE_PATH", "").strip("/")
COMFY_URL = f"{COMFY_SCHEME}://{COMFY_HOST}:{COMFY_PORT}" + (f"/{COMFY_PATH}" if COMFY_PATH else "")

LISTEN_PORT = int(os.getenv("MCP_LISTEN_PORT", "8000"))
RUN_UID = int(os.getenv("MCP_RUN_UID", "65534"))
RUN_GID = int(os.getenv("MCP_RUN_GID", "65534"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "32"))
MAX_INLINE_MB = int(os.getenv("MAX_INLINE_OUTPUT_MB", "8"))
SCAN_MAX_FILES = int(os.getenv("SCAN_MAX_FILES", "5000"))
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT_SEC", "1800"))
HF_TIMEOUT = int(os.getenv("HYPERFRAMES_TIMEOUT_SEC", "3600"))

CF_VERIFY = env_bool("CF_ACCESS_VERIFY")
CF_TEAM = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").rstrip("/")
CF_AUD = os.getenv("CF_ACCESS_AUD", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

MODELS = Path("/mnt/comfy/models").resolve()
NODES = Path("/mnt/comfy/custom_nodes").resolve()
EXPORTS = Path("/data/exports").resolve()
TMP = Path("/data/tmp").resolve()
HF_ROOT = Path(os.getenv("HYPERFRAMES_PROJECTS_ROOT", "/data/hyperframes/projects")).resolve()
for path in (EXPORTS, TMP, HF_ROOT):
    path.mkdir(parents=True, exist_ok=True)

if CF_VERIFY and (not CF_TEAM or not CF_AUD):
    raise RuntimeError("CF_ACCESS_VERIFY=true requires CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD")

mcp = MCPServer("video-mcp")


def under(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Path escapes allowed root")
    return candidate


def file_meta(file_id: str, path: Path, source: str, **details: Any) -> dict[str, Any]:
    name = path.name.split("__", 1)[-1]
    meta = {
        "file_id": file_id,
        "filename": name,
        "size_bytes": path.stat().st_size,
        "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        "source": source,
        "created_epoch": int(time.time()),
        "download_path": f"/files/{file_id}",
    }
    if PUBLIC_BASE_URL:
        meta["download_url"] = f"{PUBLIC_BASE_URL}/files/{file_id}"
    if details:
        meta["details"] = details
    (EXPORTS / f"{file_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def cached(file_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", file_id):
        raise ValueError("Invalid file_id")
    matches = list(EXPORTS.glob(f"{file_id}__*"))
    if not matches:
        raise ValueError("Cached file not found")
    return matches[0]


def target(filename: str) -> tuple[str, Path]:
    safe = Path(filename).name
    if not safe:
        raise ValueError("Invalid filename")
    file_id = uuid.uuid4().hex
    return file_id, EXPORTS / f"{file_id}__{safe}"


async def comfy(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{COMFY_URL}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 120), follow_redirects=True) as client:
        response = await client.request(method, url, **kwargs)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"ComfyUI HTTP {response.status_code}: {response.text[:4000]}") from exc
    ctype = response.headers.get("content-type", "")
    return response.json() if "json" in ctype else response.content


async def command(args: list[str], timeout: int) -> tuple[str, str]:
    binary = shutil.which(args[0])
    if not binary:
        raise RuntimeError(f"Missing binary: {args[0]}")
    proc = await asyncio.create_subprocess_exec(binary, *args[1:], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Command timed out after {timeout}s") from exc
    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    if proc.returncode:
        raise RuntimeError(stderr[-12000:] or stdout[-12000:])
    return stdout, stderr


@mcp.tool()
async def inventory_summary() -> dict[str, Any]:
    """Summarize ComfyUI reachability, loaded nodes, model folders and local runtime capabilities."""
    nodes = await comfy("GET", "object_info")
    folders = await comfy("GET", "models")
    return {
        "comfyui_url": COMFY_URL,
        "loaded_node_count": len(nodes) if isinstance(nodes, dict) else None,
        "model_folders": folders,
        "models_mount": MODELS.exists(),
        "custom_nodes_mount": NODES.exists(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "hyperframes": bool(shutil.which("hyperframes")),
    }


@mcp.tool()
async def list_loaded_nodes(search: str = "") -> dict[str, Any]:
    """Return ComfyUI node classes currently registered by /object_info, optionally filtered."""
    data = await comfy("GET", "object_info")
    if not search or not isinstance(data, dict):
        return data
    q = search.lower()
    return {k: v for k, v in data.items() if q in k.lower() or q in str(v.get("category", "")).lower()}


@mcp.tool()
async def get_node_definition(class_type: str) -> Any:
    """Return the authoritative ComfyUI definition for one loaded node class."""
    return await comfy("GET", f"object_info/{class_type}")


@mcp.tool()
async def list_model_folders() -> Any:
    """List model folders registered by ComfyUI."""
    return await comfy("GET", "models")


@mcp.tool()
async def list_registered_models(folder: str) -> Any:
    """List model names ComfyUI registered for a model folder."""
    return await comfy("GET", f"models/{folder}")


@mcp.tool()
async def scan_models(search: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Scan the read-only ComfyUI models mount and return relative paths, sizes and extensions."""
    if not MODELS.exists():
        return []
    q = search.lower()
    results = []
    for i, path in enumerate(MODELS.rglob("*")):
        if i >= SCAN_MAX_FILES or len(results) >= min(limit, 2000):
            break
        if path.is_file():
            rel = str(path.relative_to(MODELS))
            if not q or q in rel.lower():
                results.append({"path": rel, "size_bytes": path.stat().st_size, "suffix": path.suffix.lower()})
    return results


@mcp.tool()
async def scan_custom_nodes(search: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Scan the read-only custom_nodes mount to discover installed packages/files."""
    if not NODES.exists():
        return []
    q = search.lower()
    results = []
    for i, path in enumerate(NODES.rglob("*")):
        if i >= SCAN_MAX_FILES or len(results) >= min(limit, 2000):
            break
        rel = str(path.relative_to(NODES))
        if not q or q in rel.lower():
            results.append({"path": rel, "type": "dir" if path.is_dir() else "file"})
    return results


@mcp.tool()
async def read_custom_node_file(relative_path: str, max_chars: int = 50000) -> str:
    """Read a text source/documentation file inside custom_nodes, sandboxed to the read-only mount."""
    path = under(NODES, relative_path)
    if path.suffix.lower() not in {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"}:
        raise ValueError("Unsupported text file type")
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


@mcp.tool()
async def submit_workflow(workflow: dict[str, Any], client_id: str = "") -> Any:
    """Submit arbitrary ComfyUI API-format workflow JSON to /prompt."""
    body: dict[str, Any] = {"prompt": workflow}
    if client_id:
        body["client_id"] = client_id
    return await comfy("POST", "prompt", json=body)


@mcp.tool()
async def get_queue() -> Any:
    return await comfy("GET", "queue")


@mcp.tool()
async def get_history(prompt_id: str = "") -> Any:
    return await comfy("GET", f"history/{prompt_id}" if prompt_id else "history")


@mcp.tool()
async def interrupt() -> Any:
    return await comfy("POST", "interrupt", json={})


@mcp.tool()
async def upload_image_base64(filename: str, data_base64: str, overwrite: bool = False, subfolder: str = "") -> Any:
    """Upload an image to ComfyUI /upload/image from base64 content."""
    data = base64.b64decode(data_base64, validate=True)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError("Upload exceeds MAX_UPLOAD_MB")
    files = {"image": (Path(filename).name, data, mimetypes.guess_type(filename)[0] or "application/octet-stream")}
    form = {"overwrite": "true" if overwrite else "false", "subfolder": subfolder}
    return await comfy("POST", "upload/image", data=form, files=files)


@mcp.tool()
async def cache_output(filename: str, subfolder: str = "", output_type: str = "output") -> dict[str, Any]:
    """Download a ComfyUI /view output into the persistent MCP media cache."""
    blob = await comfy("GET", "view", params={"filename": filename, "subfolder": subfolder, "type": output_type})
    if not isinstance(blob, bytes):
        raise RuntimeError("ComfyUI did not return binary output")
    file_id, out = target(filename)
    out.write_bytes(blob)
    return file_meta(file_id, out, "comfyui", subfolder=subfolder, type=output_type)


@mcp.tool()
async def list_cached_outputs(limit: int = 200) -> list[dict[str, Any]]:
    metas = []
    for path in sorted(EXPORTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            metas.append(json.loads(path.read_text()))
        except Exception:
            pass
    return metas


@mcp.tool()
async def get_output_inline_base64(file_id: str) -> dict[str, Any]:
    path = cached(file_id)
    if path.stat().st_size > MAX_INLINE_MB * 1024 * 1024:
        raise ValueError("Output exceeds MAX_INLINE_OUTPUT_MB; use download URL/path instead")
    return {"file_id": file_id, "filename": path.name.split("__", 1)[-1], "data_base64": base64.b64encode(path.read_bytes()).decode()}


@mcp.tool()
async def media_probe(file_id: str) -> dict[str, Any]:
    """Probe a cached media file with ffprobe."""
    path = cached(file_id)
    out, _ = await command(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], 60)
    return json.loads(out)


@mcp.tool()
async def extract_frame(file_id: str, seconds: float, output_filename: str = "frame.png") -> dict[str, Any]:
    """Extract one frame from cached video."""
    source = cached(file_id)
    out_id, out = target(output_filename)
    await command(["ffmpeg", "-y", "-ss", str(max(0.0, seconds)), "-i", str(source), "-frames:v", "1", str(out)], FFMPEG_TIMEOUT)
    return file_meta(out_id, out, "ffmpeg.extract_frame", source_file_id=file_id, seconds=seconds)


@mcp.tool()
async def transcode_video(file_id: str, output_filename: str = "transcoded.mp4", crf: int = 18, preset: str = "medium") -> dict[str, Any]:
    """Transcode cached media to broadly compatible H.264/AAC MP4."""
    if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
        raise ValueError("Unsupported preset")
    source = cached(file_id)
    out_id, out = target(output_filename)
    await command(["ffmpeg", "-y", "-i", str(source), "-c:v", "libx264", "-preset", preset, "-crf", str(max(0, min(51, crf))), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], FFMPEG_TIMEOUT)
    return file_meta(out_id, out, "ffmpeg.transcode", source_file_id=file_id)


@mcp.tool()
async def concat_videos(file_ids: list[str], output_filename: str = "assembled.mp4") -> dict[str, Any]:
    """Normalize cached clips to a common H.264/AAC profile and concatenate them."""
    if not 2 <= len(file_ids) <= 100:
        raise ValueError("Need 2-100 clips")
    temp = Path(tempfile.mkdtemp(prefix="concat-", dir=TMP))
    out_id, out = target(output_filename)
    try:
        normalized = []
        for i, file_id in enumerate(file_ids):
            src, norm = cached(file_id), temp / f"{i:04d}.mp4"
            await command(["ffmpeg", "-y", "-i", str(src), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(norm)], FFMPEG_TIMEOUT)
            normalized.append(norm)
        listing = temp / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in normalized))
        await command(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)], FFMPEG_TIMEOUT)
        return file_meta(out_id, out, "ffmpeg.concat", source_file_ids=file_ids)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


@mcp.tool()
async def mux_audio(video_file_id: str, audio_file_id: str, output_filename: str = "muxed.mp4") -> dict[str, Any]:
    """Replace/add a cached video's audio track with another cached audio source."""
    video, audio = cached(video_file_id), cached(audio_file_id)
    out_id, out = target(output_filename)
    await command(["ffmpeg", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)], FFMPEG_TIMEOUT)
    return file_meta(out_id, out, "ffmpeg.mux_audio", video_file_id=video_file_id, audio_file_id=audio_file_id)


@mcp.tool()
async def overlay_video(base_file_id: str, overlay_file_id: str, output_filename: str = "overlay.mp4", x: int = 0, y: int = 0) -> dict[str, Any]:
    """Composite a cached overlay video (including HyperFrames alpha outputs where supported) over a base video."""
    base, overlay = cached(base_file_id), cached(overlay_file_id)
    out_id, out = target(output_filename)
    await command(["ffmpeg", "-y", "-i", str(base), "-i", str(overlay), "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:eof_action=pass[v]", "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", str(out)], FFMPEG_TIMEOUT)
    return file_meta(out_id, out, "ffmpeg.overlay", base_file_id=base_file_id, overlay_file_id=overlay_file_id)


HF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def hf_project(project_id: str) -> Path:
    if not HF_ID.fullmatch(project_id):
        raise ValueError("Invalid HyperFrames project_id")
    return under(HF_ROOT, project_id)


@mcp.tool()
async def hyperframes_info() -> dict[str, Any]:
    out, _ = await command(["hyperframes", "--version"], 60)
    return {"version": out.strip(), "projects_root": str(HF_ROOT)}


@mcp.tool()
async def hyperframes_list_projects() -> list[str]:
    return sorted(p.name for p in HF_ROOT.iterdir() if p.is_dir())


@mcp.tool()
async def hyperframes_create_project(project_id: str) -> dict[str, Any]:
    """Create a local HyperFrames project non-interactively."""
    project = hf_project(project_id)
    if project.exists():
        raise ValueError("Project already exists")
    await command(["hyperframes", "init", str(project), "--example", "blank", "--non-interactive"], HF_TIMEOUT)
    return {"project_id": project_id, "path": str(project)}


@mcp.tool()
async def hyperframes_write_text_file(project_id: str, relative_path: str, content: str) -> dict[str, Any]:
    """Write HTML/CSS/JS/JSON/Markdown inside a HyperFrames project."""
    project = hf_project(project_id)
    path = under(project, relative_path)
    if path.suffix.lower() not in {".html", ".css", ".js", ".mjs", ".json", ".md", ".txt"}:
        raise ValueError("Unsupported project text file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"project_id": project_id, "path": str(path.relative_to(project)), "chars": len(content)}


@mcp.tool()
async def hyperframes_read_text_file(project_id: str, relative_path: str, max_chars: int = 50000) -> str:
    project = hf_project(project_id)
    return under(project, relative_path).read_text(encoding="utf-8", errors="replace")[:max_chars]


@mcp.tool()
async def hyperframes_import_cached_media(project_id: str, file_id: str, destination: str) -> dict[str, Any]:
    project, source = hf_project(project_id), cached(file_id)
    dest = under(project, destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return {"project_id": project_id, "destination": str(dest.relative_to(project)), "source_file_id": file_id}


@mcp.tool()
async def hyperframes_lint(project_id: str) -> dict[str, Any]:
    project = hf_project(project_id)
    out, err = await command(["hyperframes", "lint", str(project)], HF_TIMEOUT)
    return {"stdout": out[-20000:], "stderr": err[-20000:]}


@mcp.tool()
async def hyperframes_check(project_id: str) -> dict[str, Any]:
    project = hf_project(project_id)
    out, err = await command(["hyperframes", "check", str(project)], HF_TIMEOUT)
    return {"stdout": out[-20000:], "stderr": err[-20000:]}


@mcp.tool()
async def hyperframes_render(project_id: str, output_filename: str = "hyperframes.mp4", output_format: str = "mp4", fps: int = 30) -> dict[str, Any]:
    """Render a local HyperFrames project and place the result in the shared media cache."""
    if output_format not in {"mp4", "mov", "webm", "gif"}:
        raise ValueError("Unsupported HyperFrames output format")
    project = hf_project(project_id)
    out_id, out = target(output_filename)
    await command(["hyperframes", "render", str(project), "--output", str(out), "--format", output_format, "--fps", str(max(1, min(240, fps))), "--no-browser-gpu"], HF_TIMEOUT)
    return file_meta(out_id, out, "hyperframes.render", project_id=project_id, format=output_format)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "server": "video-mcp",
        "app_version": os.getenv("VIDEO_MCP_APP_VERSION", "unknown"),
        "comfyui_url": COMFY_URL,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "hyperframes": bool(shutil.which("hyperframes")),
        "cf_access_verify": CF_VERIFY,
        "uid": os.getuid(),
        "gid": os.getgid(),
    })


async def download(request: Request):
    try:
        path = cached(request.path_params["file_id"])
    except ValueError:
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(path, filename=path.name.split("__", 1)[-1])


class CloudflareJWT:
    def __init__(self, app):
        self.app = app
        self.keys = PyJWKClient(f"{CF_TEAM}/cdn-cgi/access/certs") if CF_VERIFY else None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not CF_VERIFY or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = headers.get("cf-access-jwt-assertion")
        if not token:
            return await PlainTextResponse("Missing Cloudflare Access JWT", status_code=403)(scope, receive, send)
        try:
            key = await asyncio.to_thread(self.keys.get_signing_key_from_jwt, token)  # type: ignore[union-attr]
            await asyncio.to_thread(jwt.decode, token, key.key, algorithms=["RS256"], audience=CF_AUD, issuer=CF_TEAM)
        except Exception:
            return await PlainTextResponse("Invalid Cloudflare Access JWT", status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)


transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp_app = mcp.streamable_http_app(transport_security=transport_security)


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    async with mcp.session_manager.run():
        yield


app = CloudflareJWT(Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/files/{file_id}", download, methods=["GET", "HEAD"]),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
))


def drop_privileges() -> None:
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(RUN_GID)
        os.setuid(RUN_UID)


if __name__ == "__main__":
    drop_privileges()
    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT, proxy_headers=True, forwarded_allow_ips="*")
