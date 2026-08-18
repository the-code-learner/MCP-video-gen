# Optional Blender bridge

Blender is an **external optional backend**. MCP Video Gen does not install Blender in the container and does not require Blender to start.

The recommended topology is:

```text
MCP client / AI
       |
       v
MCP Video Gen container
       |
       | HTTP + bearer token
       v
Blender bridge on the Docker host/VM
       |
       v
blender --background --python job.py
```

Blender embeds Python and exposes its scene/data API through `bpy`. Running Blender in background mode lets the bridge create or modify scenes, animate objects/cameras/lights, save `.blend` files, export 3D assets, or render images/animations without opening the desktop UI.

## Why a host bridge instead of mounting the Blender binary

The MCP container should not mount arbitrary host executables or the host root filesystem. The bridge gives the container one authenticated, narrow transport:

- create a job;
- upload only declared MCP-cache inputs into that job;
- execute one Blender Python script;
- download only declared output paths;
- never expose the VM filesystem through the MCP protocol.

The reference bridge is `scripts/blender_bridge.py` and uses only the Python standard library on the host.

## Security boundary

`bpy` scripting is intentionally powerful and Blender Python is **not a sandbox**. A submitted script can use normal Python modules available to Blender. Therefore the bridge must run as a dedicated low-privilege account which has no access to secrets, user home directories, Docker sockets, SSH keys, or unrelated project data.

A hardened systemd example is provided at:

```text
deploy/blender-bridge.service.example
```

The bridge refuses to start without `BLENDER_BRIDGE_TOKEN` and every endpoint requires the bearer token.

Recommended host layout:

```text
/opt/mcp-video-gen/scripts/blender_bridge.py
/var/lib/mcp-blender-bridge/              # writable only by blender-mcp
/etc/mcp-blender-bridge.env               # root-owned, contains token
```

Example environment file:

```text
BLENDER_BRIDGE_BIND=0.0.0.0
BLENDER_BRIDGE_PORT=9876
BLENDER_BRIDGE_TOKEN=<long-random-secret>
BLENDER_BINARY=/usr/bin/blender
BLENDER_BRIDGE_ROOT=/var/lib/mcp-blender-bridge
BLENDER_BRIDGE_MAX_INPUT_MB=512
BLENDER_BRIDGE_MAX_SCRIPT_CHARS=500000
BLENDER_BRIDGE_MAX_TIMEOUT_SEC=7200
```

If the bridge binds beyond loopback, use the VM firewall to restrict port `9876` to the Docker-host/container network. Do not expose the bridge on the public Internet. Cloudflare should expose the MCP endpoint, not the Blender bridge.

## Portainer variables

The public Stack keeps Blender disabled by default:

```text
BLENDER_ENABLED=false
BLENDER_BRIDGE_URL=http://host.docker.internal:9876
BLENDER_BRIDGE_TOKEN=
BLENDER_BRIDGE_TIMEOUT_SEC=7200
```

After the host bridge is running, set:

```text
BLENDER_ENABLED=true
BLENDER_BRIDGE_URL=http://host.docker.internal:9876
BLENDER_BRIDGE_TOKEN=<same secret used by the host bridge>
```

If Blender is disabled, missing, or temporarily unreachable, Blender MCP tools return a structured `available=false` result. The rest of MCP Video Gen continues to work.

## MCP tools

### `blender_info`

Checks whether the optional bridge and Blender executable are available and returns the detected Blender version.

### `blender_execute_python`

General-purpose Blender automation. The client supplies Python source plus optional MCP-cache file IDs.

Inputs are uploaded to:

```python
os.environ["BLENDER_INPUT_DIR"]
```

The script writes declared results below:

```python
os.environ["BLENDER_OUTPUT_DIR"]
```

Every declared output is downloaded back into the MCP media cache and receives a normal `file_id`.

This is the primitive that allows an AI to procedurally create meshes, geometry nodes, materials, rigging, cameras, lights and animation; save `.blend`; export arbitrary supported formats; or produce render passes.

### `blender_render_blend`

Convenience wrapper for rendering one frame from a cached `.blend` file to PNG/JPEG/EXR/WebP.

### `blender_render_animation`

Convenience wrapper for rendering a frame range from a cached `.blend` file to H.264 MP4.

### `blender_export_glb`

Convenience wrapper for exporting a cached `.blend` scene to binary glTF (`.glb`). The general Python tool can be used for other import/export operators supported by the installed Blender version.

## File flow

Blender uses the same MCP cache contract as every other backend:

```text
AI/client
   |
   | cache_file_base64 / cache_text_file / chunked upload
   v
MCP cache (file_id)
   |
   +------> Blender bridge input
   |             |
   |             +--> .blend / .glb / render / animation
   |                         |
   |                         v
   +<---------------- MCP cache (new file_id)
   |
   +------> ComfyUI input
   +------> FFmpeg / OpenCV
   +------> HyperFrames
   +------> timeline / subtitles / audio tools
   |
   v
AI/client via /files/{file_id}, inline base64, or chunked base64 reads
```

This makes Blender useful both as a final renderer and as an intermediate generator. For example, the AI can create a simple 3D blocking animation, render depth/normal/albedo passes, cache them, and pass those files into a ComfyUI workflow as control/reference material for image or video generation.
