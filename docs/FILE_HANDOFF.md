# Native file handoff and routing

MCP Video Gen keeps `file_id` as the canonical identifier for every cached artifact. Binary payloads should **not** normally be moved through tool-result JSON as base64.

The preferred transfer order is:

1. native file/resource reference where the client and server can actually use it;
2. authenticated HTTP(S) streaming;
3. MCP `resources/read` as a compatibility path;
4. bounded chunked base64 as a fallback/debug interoperability path;
5. inline base64 only for small compatibility cases.

Never resize, recompress, transcode, or otherwise alter an artifact solely to make it fit through a tool result.

## The most important ComfyUI rule

Standard ComfyUI `LoadImage` does **not** accept:

- an HTTP or HTTPS URL;
- an MCP `ResourceLink`;
- a `media://cache/...` URI;
- `/files/<file_id>`;
- an MCP cache `file_id`.

It expects a filename/path that already exists in ComfyUI's input namespace.

Therefore the canonical image route is always:

```text
external/client image
        ↓
MCP cache -> file_id
        ↓
comfy_upload_cached_image(file_id)
        ↓ multipart/form-data
ComfyUI /upload/image -> /input
        ↓
workflow_load_image_value
        ↓
LoadImage.inputs.image
```

`submit_workflow` has a guard for standard `LoadImage`: obvious URLs, MCP resource URIs, `/files/...` paths and 32-character MCP `file_id` values are rejected with routing instructions rather than being sent to ComfyUI.

## Routing matrix

| From | To | Preferred route | Result / next step |
|---|---|---|---|
| Client/ChatGPT retrievable HTTPS file | MCP cache | `import_remote_file(uri)` | returns `file_id` |
| Client file with no server-retrievable URL | MCP cache | client-supported upload; compatibility fallback is `cache_file_base64` or chunked upload | returns `file_id` |
| MCP cache image | ComfyUI `/input` | `comfy_upload_cached_image(file_id)` | use returned `workflow_load_image_value` in `LoadImage` |
| ComfyUI output | MCP cache | `cache_output(...)` | returns `file_id` |
| MCP cache | Client/ChatGPT | `get_cached_file_resource(file_id)` | `ResourceLink`; HTTP stream preferred when configured |
| MCP cache | Blender | pass `file_id` to Blender tool/input mapping | bridge streams bytes server-side |
| Blender | MCP cache | declared Blender outputs | returned automatically as `file_id` values |
| MCP cache | HyperFrames | `hyperframes_import_cached_media(...)` | media copied into project |
| MCP cache audio/video | ComfyUI | media-specific installed node/API only | no generic standard adapter yet |

The MCP also exposes `file_transfer_guide()`. Its tool description and structured result repeat these rules so an AI can query the routing policy at runtime.

## Client -> MCP cache: preferred HTTPS reference ingress

When the client provides a **real HTTPS URL that the MCP server itself is allowed and able to fetch**, use:

```text
import_remote_file(uri, filename="", expected_size_bytes=0, expected_sha256="")
```

The file is streamed directly to a temporary file and atomically promoted into the persistent MCP cache. The payload does not pass through the language-model/tool JSON as base64.

Security behavior:

- HTTPS is required;
- embedded URL credentials are rejected;
- DNS is resolved and non-public/private/loopback/link-local/reserved addresses are rejected;
- every redirect target is revalidated;
- redirects are bounded;
- `Content-Length` is checked when present;
- an independent streaming byte limit is enforced even when `Content-Length` is absent or wrong;
- optional expected byte length and SHA-256 can be verified before cache promotion;
- partial files are removed on failure;
- full signed URLs/query strings are not persisted in cache metadata.

Optional deployment hardening:

```text
REMOTE_IMPORT_ALLOWED_HOSTS=files.example.com,*.storage.example.com
REMOTE_IMPORT_MAX_REDIRECTS=5
REMOTE_IMPORT_TIMEOUT_SEC=120
```

If `REMOTE_IMPORT_ALLOWED_HOSTS` is empty, any host that resolves only to public/global addresses is eligible. An allowlist is recommended when the deployment uses known file providers.

### Important limitation

A ChatGPT/OpenAI attachment `file_id` is not automatically a URL that an arbitrary remote MCP server can dereference. Do not invent a URL and do not pass an opaque file ID to ComfyUI.

Use `import_remote_file` only when the client exposes an actual server-retrievable HTTPS reference. Otherwise use whatever upload mechanism the client integration supports; the legacy base64/chunked tools remain as interoperability fallbacks.

## Existing compatibility upload tools

The compatibility client -> cache tools remain:

```text
cache_file_base64
file_upload_begin
file_upload_chunk
file_upload_finish
file_upload_abort
```

They provide bounds, contiguous offsets and optional SHA-256 validation. Their binary chunks are still base64, so they are not the preferred path when a retrievable native/HTTPS file reference exists.

## MCP cache -> ComfyUI `/input`

For images, use:

```text
comfy_upload_cached_image(file_id, overwrite=false, subfolder="")
```

This function:

1. resolves `file_id` inside the MCP cache;
2. verifies that the cached file is an image according to its MIME type;
3. opens the cached file server-side;
4. submits it to ComfyUI `/upload/image` as multipart binary data;
5. returns the actual ComfyUI input metadata;
6. returns `workflow_load_image_value` for the next workflow.

Example result shape:

```json
{
  "ok": true,
  "status": "uploaded",
  "source_file_id": "...",
  "comfyui_input": {
    "name": "reference.png",
    "subfolder": "references",
    "type": "input"
  },
  "workflow_load_image_value": "references/reference.png"
}
```

Then use:

```json
{
  "class_type": "LoadImage",
  "inputs": {
    "image": "references/reference.png"
  }
}
```

Never substitute the original URL, `file_id`, ResourceLink, or `/files/...` path for that value.

### Audio/video into ComfyUI

There is currently no generic standard MCP adapter that can place arbitrary audio/video into every ComfyUI custom node's expected input contract. Do not work around that by inserting an HTTP URL into an unrelated node.

Instead:

- introspect the installed node that is meant to consume the media;
- use its documented API/input mechanism if it explicitly supports URLs or uploads;
- or add a dedicated server-side cache adapter for that media type.

The existence of a cached `file_id` does not by itself make the file a valid input for every ComfyUI node.

## ComfyUI -> MCP cache

After `submit_workflow`, poll history until the expected output exists, then cache it with:

```text
cache_output(filename, subfolder, output_type)
```

The result is a normal MCP cache entry identified by `file_id`. From that point the artifact can flow through FFmpeg, Blender, HyperFrames, subtitles, timelines, analysis tools, or back to the client without another ComfyUI download through the model context.

## MCP cache -> client

Use:

```text
get_cached_file_resource(file_id)
```

The tool returns an MCP `ResourceLink` containing the artifact name, MIME type and byte size without embedding the file bytes in the tool result.

When `PUBLIC_BASE_URL` is configured with an HTTP(S) origin, the returned URI is:

```text
<PUBLIC_BASE_URL>/files/<file_id>
```

This is the preferred path for large images, video, audio, `.blend`, GLB, archives and other binary artifacts. The Starlette file route supports `GET`, `HEAD`, `Content-Length`, `Content-Disposition`, validators and byte-range requests, so clients can stream or resume large files without routing the body through the model context.

If `PUBLIC_BASE_URL` is not configured, the tool falls back to:

```text
media://cache/<file_id>
```

The MCP server registers the matching resource template:

```text
media://cache/{file_id}
```

A client can use `resources/read` as a compatibility mechanism. This is **not** the preferred path for large binary files: MCP binary resource contents are represented as a base64 blob on the wire.

Existing fallback tools:

```text
get_cached_file_info
read_cached_file_chunk_base64
get_output_inline_base64
```

## MCP cache <-> Blender

Blender integration already follows the correct reference-first model.

For cache -> Blender, Blender MCP tools accept `file_id` values and the MCP streams those cached bytes to the authenticated host bridge. The AI should not create temporary public links.

For Blender -> cache, declared outputs are downloaded by the MCP server and automatically promoted to new cache `file_id` values.

## MCP cache -> HyperFrames

Use:

```text
hyperframes_import_cached_media(project_id, file_id, destination)
```

The media is copied server-side from the shared cache into the project. Do not read the file through base64 and rewrite it into HyperFrames.

## HTTP authentication

A `ResourceLink` does not grant authorization by itself. `/files/{file_id}` remains behind the deployment's normal authentication/proxy policy. Clients must be able to retrieve the HTTP(S) URI with the appropriate authorization context.

If a client cannot use the authenticated HTTP URI, `media://cache/{file_id}` + MCP `resources/read` remains the protocol-level compatibility fallback.

## Large-file rule

There is deliberately no artificial output-size limit imposed by inline JSON/base64 on the preferred outbound path. Effective limits still come from the client, reverse proxy, filesystem, HTTP stack and deployment policy.

Inbound remote imports remain bounded by `MAX_UPLOAD_MB` because the server must protect its cache and disk. Increase that deployment setting deliberately for larger trusted imports rather than bypassing it through base64 or an unvalidated downloader.
