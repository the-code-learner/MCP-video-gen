from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

import httpx


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def _unavailable(backend: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "backend": backend,
        "status": "unavailable",
        "message": message,
        **details,
    }


def _failed(backend: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "available": True,
        "backend": backend,
        "status": "operation_failed",
        "message": message,
        **details,
    }


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError("Path must be a relative path without '..'")
    return str(path)


async def _file_chunks(path: Path, chunk_size: int = 1024 * 1024):
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def register_external_backend_tools(
    mcp: Any,
    *,
    server_module: Any,
    cached: Callable[[str], Path],
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
) -> None:
    """Make external backends optional and add the Blender host bridge adapter."""

    blender_enabled = _bool_env("BLENDER_ENABLED", False)
    blender_url = os.getenv("BLENDER_BRIDGE_URL", "http://host.docker.internal:9876").rstrip("/")
    blender_token = os.getenv("BLENDER_BRIDGE_TOKEN", "")
    blender_timeout = int(os.getenv("BLENDER_BRIDGE_TIMEOUT_SEC", "900"))

    async def comfy_status() -> dict[str, Any]:
        try:
            data = await server_module.comfy("GET", "object_info", timeout=5)
            count = len(data) if isinstance(data, dict) else None
            return {
                "ok": True,
                "available": True,
                "backend": "comfyui",
                "status": "available",
                "endpoint": server_module.COMFY_URL,
                "loaded_node_count": count,
            }
        except Exception as exc:
            return _unavailable(
                "comfyui",
                f"ComfyUI is not currently reachable through the MCP: {exc}",
                endpoint=server_module.COMFY_URL,
            )

    async def comfy_call(fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        status = await comfy_status()
        if not status["available"]:
            return status
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            # External backend failures are returned as model-readable results
            # rather than surfacing as MCP tool errors.
            return _failed("comfyui", str(exc), endpoint=server_module.COMFY_URL)

    async def blender_status_impl() -> dict[str, Any]:
        if not blender_enabled:
            return _unavailable(
                "blender",
                "Blender integration is disabled. Set BLENDER_ENABLED=true after installing/configuring the host bridge.",
                configured=False,
                endpoint=blender_url,
            )
        if not blender_token:
            return _unavailable(
                "blender",
                "BLENDER_ENABLED=true but BLENDER_BRIDGE_TOKEN is not configured.",
                configured=False,
                endpoint=blender_url,
            )
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.get(
                    f"{blender_url}/v1/health",
                    headers={"Authorization": f"Bearer {blender_token}"},
                )
                response.raise_for_status()
                data = response.json()
            return {
                "ok": True,
                "available": True,
                "backend": "blender",
                "status": "available",
                "endpoint": blender_url,
                **data,
            }
        except Exception as exc:
            return _unavailable(
                "blender",
                f"Blender bridge is configured but not currently reachable through the MCP: {exc}",
                configured=True,
                endpoint=blender_url,
            )

    async def blender_job(
        *,
        script: str,
        input_files: dict[str, str],
        output_files: list[str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        status = await blender_status_impl()
        if not status["available"]:
            return status
        if len(script) > 500_000:
            raise ValueError("Blender Python script exceeds 500000 characters")
        normalized_inputs = {_safe_relative(name): file_id for name, file_id in input_files.items()}
        normalized_outputs = [_safe_relative(name) for name in output_files]
        headers = {"Authorization": f"Bearer {blender_token}"}
        job_id = ""
        try:
            timeout = httpx.Timeout(
                max(30, min(blender_timeout, timeout_seconds + 30)),
                connect=10,
            )
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                created = await client.post(
                    f"{blender_url}/v1/jobs",
                    headers=headers,
                    json={
                        "script": script,
                        "expected_outputs": normalized_outputs,
                        "timeout_seconds": max(1, min(blender_timeout, timeout_seconds)),
                    },
                )
                created.raise_for_status()
                job_id = str(created.json()["job_id"])

                for remote_name, file_id in normalized_inputs.items():
                    source = cached(file_id)
                    uploaded = await client.put(
                        f"{blender_url}/v1/jobs/{job_id}/inputs/{remote_name}",
                        headers={**headers, "Content-Type": "application/octet-stream"},
                        content=_file_chunks(source),
                    )
                    uploaded.raise_for_status()

                ran = await client.post(
                    f"{blender_url}/v1/jobs/{job_id}/run",
                    headers=headers,
                )
                ran.raise_for_status()
                run_data = ran.json()
                cached_outputs: list[dict[str, Any]] = []

                for remote_name in normalized_outputs:
                    output_id, output_path = target(Path(remote_name).name)
                    try:
                        async with client.stream(
                            "GET",
                            f"{blender_url}/v1/jobs/{job_id}/outputs/{remote_name}",
                            headers=headers,
                        ) as downloaded:
                            if downloaded.status_code == 404:
                                continue
                            downloaded.raise_for_status()
                            with output_path.open("wb") as handle:
                                async for chunk in downloaded.aiter_bytes():
                                    handle.write(chunk)
                    except Exception:
                        output_path.unlink(missing_ok=True)
                        raise
                    cached_outputs.append(
                        file_meta(
                            output_id,
                            output_path,
                            "blender",
                            blender_job_id=job_id,
                            remote_output=remote_name,
                        )
                    )

                return {
                    "ok": bool(run_data.get("ok", False)),
                    "available": True,
                    "backend": "blender",
                    "status": "completed" if run_data.get("ok") else "operation_failed",
                    "job": run_data,
                    "outputs": cached_outputs,
                }
        except Exception as exc:
            current = await blender_status_impl()
            if not current["available"]:
                return current
            return _failed("blender", str(exc), endpoint=blender_url, job_id=job_id or None)

    # Replace network-dependent ComfyUI tools with availability-aware wrappers.
    originals = {
        name: getattr(server_module, name)
        for name in (
            "list_loaded_nodes",
            "get_node_definition",
            "list_model_folders",
            "list_registered_models",
            "submit_workflow",
            "get_queue",
            "get_history",
            "interrupt",
            "upload_image_base64",
            "cache_output",
        )
    }
    for name in originals:
        try:
            mcp.remove_tool(name)
        except Exception:
            pass

    async def list_loaded_nodes(search: str = "") -> Any:
        """List ComfyUI nodes, or report that ComfyUI is currently unavailable."""
        return await comfy_call(originals["list_loaded_nodes"], search)

    async def get_node_definition(class_type: str) -> Any:
        """Get one ComfyUI node definition, or report backend availability."""
        return await comfy_call(originals["get_node_definition"], class_type)

    async def list_model_folders() -> Any:
        """List ComfyUI model folders, or report backend availability."""
        return await comfy_call(originals["list_model_folders"])

    async def list_registered_models(folder: str) -> Any:
        """List ComfyUI registered models, or report backend availability."""
        return await comfy_call(originals["list_registered_models"], folder)

    async def submit_workflow(workflow: dict[str, Any], client_id: str = "") -> Any:
        """Submit a ComfyUI workflow, soft-failing when the backend is unavailable."""
        return await comfy_call(originals["submit_workflow"], workflow, client_id)

    async def get_queue() -> Any:
        """Get the ComfyUI queue, or report backend availability."""
        return await comfy_call(originals["get_queue"])

    async def get_history(prompt_id: str = "") -> Any:
        """Get ComfyUI history, or report backend availability."""
        return await comfy_call(originals["get_history"], prompt_id)

    async def interrupt() -> Any:
        """Interrupt ComfyUI, or report backend availability."""
        return await comfy_call(originals["interrupt"])

    async def upload_image_base64(
        filename: str,
        data_base64: str,
        overwrite: bool = False,
        subfolder: str = "",
    ) -> Any:
        """Upload an image to ComfyUI, or report backend availability."""
        return await comfy_call(
            originals["upload_image_base64"], filename, data_base64, overwrite, subfolder
        )

    async def cache_output(
        filename: str,
        subfolder: str = "",
        output_type: str = "output",
    ) -> Any:
        """Cache a ComfyUI output, or report backend availability."""
        return await comfy_call(originals["cache_output"], filename, subfolder, output_type)

    for name, fn in (
        ("list_loaded_nodes", list_loaded_nodes),
        ("get_node_definition", get_node_definition),
        ("list_model_folders", list_model_folders),
        ("list_registered_models", list_registered_models),
        ("submit_workflow", submit_workflow),
        ("get_queue", get_queue),
        ("get_history", get_history),
        ("interrupt", interrupt),
        ("upload_image_base64", upload_image_base64),
        ("cache_output", cache_output),
    ):
        mcp.add_tool(fn, name=name)

    # Replace inventory_summary so a missing external backend never makes the
    # discovery call itself fail.
    try:
        mcp.remove_tool("inventory_summary")
    except Exception:
        pass

    @mcp.tool(name="inventory_summary")
    async def inventory_summary() -> dict[str, Any]:
        """Summarize local capabilities and availability of optional external backends."""
        comfy = await comfy_status()
        blender = await blender_status_impl()
        folders: Any = None
        if comfy["available"]:
            try:
                folders = await server_module.comfy("GET", "models", timeout=10)
            except Exception as exc:
                folders = _failed("comfyui", str(exc))
        return {
            "external_backends": {"comfyui": comfy, "blender": blender},
            "model_folders": folders,
            "models_mount": server_module.MODELS.exists(),
            "custom_nodes_mount": server_module.NODES.exists(),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "hyperframes": bool(shutil.which("hyperframes")),
        }

    @mcp.tool()
    async def external_backends_status() -> dict[str, Any]:
        """Return availability and configuration status for every external execution backend."""
        return {
            "comfyui": await comfy_status(),
            "blender": await blender_status_impl(),
        }

    @mcp.tool()
    async def blender_info() -> dict[str, Any]:
        """Return Blender bridge availability/version without failing when Blender is absent."""
        return await blender_status_impl()

    @mcp.tool()
    async def blender_execute_python(
        script: str,
        input_files: dict[str, str] | None = None,
        output_files: list[str] | None = None,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Execute a Blender Python job on the optional host bridge.

        `input_files` maps bridge-relative filenames (for example
        `texture.png`) to MCP cache file IDs. Inside Blender they are available
        below `os.environ['BLENDER_INPUT_DIR']`. Write requested results below
        `os.environ['BLENDER_OUTPUT_DIR']` and list those relative paths in
        `output_files`; the MCP automatically copies them back into its cache.

        This is intentionally powerful: the bridge must run as a dedicated,
        low-privilege OS user because Blender Python is not an application
        sandbox.
        """
        return await blender_job(
            script=script,
            input_files=input_files or {},
            output_files=output_files or [],
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool()
    async def blender_render_blend(
        file_id: str,
        frame: int = 1,
        output_filename: str = "render.png",
        engine: str = "",
        resolution_percentage: int = 100,
        timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        """Render one frame from a cached .blend file through the optional Blender bridge."""
        suffix = Path(output_filename).suffix.lower()
        formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".exr": "OPEN_EXR", ".webp": "WEBP"}
        if suffix not in formats:
            raise ValueError("output_filename must end in .png, .jpg/.jpeg, .exr, or .webp")
        script = f'''import bpy, os\nsrc=os.path.join(os.environ["BLENDER_INPUT_DIR"], "source.blend")\nout=os.path.join(os.environ["BLENDER_OUTPUT_DIR"], {json.dumps(Path(output_filename).name)})\nbpy.ops.wm.open_mainfile(filepath=src, load_ui=False)\nscene=bpy.context.scene\nscene.frame_set({int(frame)})\nscene.render.resolution_percentage={max(1, min(100, int(resolution_percentage)))}\nscene.render.image_settings.file_format={json.dumps(formats[suffix])}\nscene.render.filepath=out\n''' + (f'scene.render.engine={json.dumps(engine)}\n' if engine else '') + 'bpy.ops.render.render(write_still=True)\n'
        return await blender_job(
            script=script,
            input_files={"source.blend": file_id},
            output_files=[Path(output_filename).name],
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool()
    async def blender_render_animation(
        file_id: str,
        start_frame: int = 1,
        end_frame: int = 120,
        output_filename: str = "animation.mp4",
        engine: str = "",
        fps: int = 30,
        resolution_percentage: int = 100,
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Render a cached .blend animation to H.264 MP4 through the optional Blender bridge."""
        if Path(output_filename).suffix.lower() != ".mp4":
            raise ValueError("output_filename must end in .mp4")
        if end_frame < start_frame:
            raise ValueError("end_frame must be >= start_frame")
        script = f'''import bpy, os\nsrc=os.path.join(os.environ["BLENDER_INPUT_DIR"], "source.blend")\nout=os.path.join(os.environ["BLENDER_OUTPUT_DIR"], {json.dumps(Path(output_filename).name)})\nbpy.ops.wm.open_mainfile(filepath=src, load_ui=False)\nscene=bpy.context.scene\nscene.frame_start={int(start_frame)}\nscene.frame_end={int(end_frame)}\nscene.render.fps={max(1, min(240, int(fps)))}\nscene.render.resolution_percentage={max(1, min(100, int(resolution_percentage)))}\nscene.render.image_settings.file_format="FFMPEG"\nscene.render.ffmpeg.format="MPEG4"\nscene.render.ffmpeg.codec="H264"\nscene.render.filepath=out\n''' + (f'scene.render.engine={json.dumps(engine)}\n' if engine else '') + 'bpy.ops.render.render(animation=True)\n'
        return await blender_job(
            script=script,
            input_files={"source.blend": file_id},
            output_files=[Path(output_filename).name],
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool()
    async def blender_export_glb(
        file_id: str,
        output_filename: str = "scene.glb",
        timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        """Export a cached .blend scene, including animation when present, as binary glTF/GLB."""
        if Path(output_filename).suffix.lower() != ".glb":
            raise ValueError("output_filename must end in .glb")
        script = f'''import bpy, os\nsrc=os.path.join(os.environ["BLENDER_INPUT_DIR"], "source.blend")\nout=os.path.join(os.environ["BLENDER_OUTPUT_DIR"], {json.dumps(Path(output_filename).name)})\nbpy.ops.wm.open_mainfile(filepath=src, load_ui=False)\nbpy.ops.export_scene.gltf(filepath=out, export_format="GLB")\n'''
        return await blender_job(
            script=script,
            input_files={"source.blend": file_id},
            output_files=[Path(output_filename).name],
            timeout_seconds=timeout_seconds,
        )
