# Native file handoff and routing

MCP Video Gen keeps `file_id` as the canonical identifier for every cached artifact. Binary payloads should **not** normally be moved through model/tool JSON as base64.

The preferred transfer order is:

1. ChatGPT native file parameters (`openai/fileParams`) for files attached in ChatGPT;
2. server-retrievable HTTPS streaming for generic remote files;
3. native MCP `ResourceLink` / authenticated HTTP streaming for cache -> client delivery;
4. MCP `resources/read` as a compatibility path;
5. outbound base64 read tools only for compatibility/debugging.

Model-mediated binary **upload** through Base64/chunk tool calls is intentionally not exposed in v3.0.0.

Never resize, recompress, transcode, or otherwise alter an artifact solely to make it fit through a tool result.

## The most important ComfyUI rule

Standard ComfyUI `LoadImage` does **not** accept:

- an HTTP or HTTPS URL;
- an MCP `ResourceLink`;
- a `media://cache/...` URI;
- `/files/<file_id>`;
- a ChatGPT/OpenAI file ID;
- an MCP cache `file_id`.

It expects a filename/path that already exists in ComfyUI's input namespace.

Therefore the canonical image route is:

```text
ChatGPT attachment / external image
        ↓
MCP cache -> file_id
        ↓
comfy_upload_cached_media(file_id)
        ↓ multipart/form-data
ComfyUI input namespace
        ↓
workflow_load_image_value
        ↓
LoadImage.inputs.image
```

`submit_workflow` has a guard for standard `LoadImage`: obvious URLs, MCP resource URIs, `/files/...` paths and 32-character MCP `file_id` values are rejected with routing instructions rather than being sent to ComfyUI.

## Routing matrix

| From | To | Preferred route | Result / next step |
|---|---|---|---|
| ChatGPT attached file | MCP cache | `save_uploaded_file(file)` / `save_uploaded_files(files)` via `openai/fileParams` | returns `file_id` |
| Generic retrievable HTTPS file | MCP cache | `import_remote_file(uri)` | returns `file_id` |
| Model-authored UTF-8 text | MCP cache | `cache_text_file(...)` | returns `file_id` |
| MCP cache media | ComfyUI input namespace | `comfy_upload_cached_media(file_id)` | use returned `workflow_input_value`; images also get `workflow_load_image_value` |
| ComfyUI output | MCP cache | `cache_output(...)` | returns `file_id` |
| MCP cache | Client/ChatGPT | `get_cached_file_resource(file_id)` | `ResourceLink`; HTTP stream preferred when configured |
| MCP cache | Blender | pass `file_id` to Blender tool/input mapping | bridge streams bytes server-side |
| Blender | MCP cache | declared Blender outputs | returned automatically as `file_id` values |
| MCP cache | HyperFrames | `hyperframes_import_cached_media(...)` | media copied into project |

The MCP also exposes `file_transfer_guide()`. Its tool description and structured result repeat these rules so an AI can query the routing policy at runtime.

## ChatGPT attachment -> MCP cache

For a file attached directly in ChatGPT, use:

```text
save_uploaded_file(file)
```

For a batch:

```text
save_uploaded_files(files)
```

These tools declare `_meta["openai/fileParams"]`. A compatible ChatGPT client binds the selected attachment and supplies a temporary authorized object containing:

```text
download_url
file_id
mime_type? 
file_name?
```

The model should not construct this object manually when the client can bind an attachment natively.

Video Gen streams the temporary HTTPS URL directly to a `.part` file, enforcing `MAX_UPLOAD_MB` during transfer and computing SHA-256 as bytes arrive. The completed file is then promoted into the normal persistent cache and receives a canonical Video Gen `file_id`.

The attachment bytes never need to become a Base64 argument in model/tool context.

Security behavior:

- HTTPS is required;
- embedded URL credentials are rejected;
- only HTTPS port 443 is accepted;
- DNS is resolved and non-public/private/loopback/link-local/reserved addresses are rejected;
- every redirect target is revalidated;
- redirects and timeouts are bounded;
- `Content-Length` is checked when present;
- an independent streaming byte limit is enforced even when `Content-Length` is absent or wrong;
- SHA-256 is computed during streaming;
- partial files are removed on failure;
- temporary URL query strings are stripped by the audit sanitizer and the generic `httpx`/`httpcore` INFO request loggers are suppressed to avoid leaking bearer-like signed URLs.

Optional controls:

```text
CHATGPT_FILE_TIMEOUT_SEC=300
CHATGPT_FILE_MAX_REDIRECTS=5
CHATGPT_FILE_MAX_BATCH_FILES=20
```

See `docs/CHATGPT_FILE_UPLOAD.md` for details.

## Generic HTTPS -> MCP cache

When a non-ChatGPT source provides a **real HTTPS URL that the MCP server itself is allowed and able to fetch**, use:

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

If `REMOTE_IMPORT_ALLOWED_HOSTS` is empty, any host that resolves only to public/global addresses is eligible. An allowlist is recommended when the deployment uses known generic file providers.

Do not invent a URL from an opaque ChatGPT/OpenAI file ID or another MCP's proprietary file ID. For a ChatGPT attachment use the native file-param tools. For another MCP/service, use `import_remote_file` only when it actually exposes a retrievable HTTPS reference.

## Removed binary client-upload tools

v3.0.0 removes:

```text
cache_file_base64
file_upload_begin
file_upload_status
file_upload_chunk_auto
file_upload_chunk
file_upload_finish
file_upload_abort
```

They previously transported binary Base64 through tool arguments and could require repeated model-mediated calls. Native ChatGPT file parameters make that path unnecessary for the normal ChatGPT workflow.

`cache_text_file` remains because it stores UTF-8 text authored by the model/client rather than transporting an attachment. The WebGUI browser upload remains available as an administrative/recovery path.

## MCP cache -> ComfyUI input namespace

Use the generic staging tool:

```text
comfy_upload_cached_media(file_id, overwrite=false, subfolder="")
```

This function resolves `file_id` inside the MCP cache and uploads the original cached bytes server-side to ComfyUI's input namespace as multipart data. It returns:

```text
workflow_input_value
```

For images it also returns:

```text
workflow_load_image_value
```

Use the latter in standard `LoadImage.inputs.image`.

The older `comfy_upload_cached_image` remains as a backward-compatible image-only alias.

### Audio/video into ComfyUI

Staging is generic, but loader semantics are node-specific. After `comfy_upload_cached_media(file_id)`, inspect the actual installed consumer node with:

```text
list_loaded_nodes
get_node_definition
```

Use `workflow_input_value` only for a parameter that truly expects an input filename/path. Do not work around unknown loader semantics by inserting an HTTP URL into an unrelated node.

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

Existing outbound fallback tools:

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

Inbound native ChatGPT downloads and generic remote imports remain bounded by `MAX_UPLOAD_MB` because the server must protect its cache and disk. Increase that deployment setting deliberately for larger trusted media rather than bypassing it through an unvalidated downloader.
