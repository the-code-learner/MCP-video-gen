# Native file handoff

MCP Video Gen keeps `file_id` as the canonical identifier for every cached artifact, but binary payloads should **not** normally be moved through tool-result JSON as base64.

The preferred transfer order is:

1. native MCP `ResourceLink` / file reference;
2. authenticated HTTP(S) streaming from `/files/{file_id}`;
3. MCP `resources/read` as a compatibility path;
4. bounded chunked base64 as a fallback/debug interoperability path;
5. inline base64 only for small compatibility cases.

Never resize, recompress, transcode, or otherwise alter an artifact solely to make it fit through a tool result.

## MCP -> client

Use:

```text
get_cached_file_resource(file_id)
```

The tool returns an MCP `ResourceLink` containing the artifact name, MIME type and byte size without embedding the file bytes in the tool result.

When `PUBLIC_BASE_URL` is configured with an HTTP(S) origin, the returned URI is:

```text
<PUBLIC_BASE_URL>/files/<file_id>
```

This is the preferred path for large images, video, audio, `.blend`, GLB, archives and other binary artifacts. The existing Starlette `FileResponse` route supports `GET`, `HEAD`, `Content-Length`, `Content-Disposition`, validators and byte-range requests, so clients can stream or resume large files without routing the body through the model context.

If `PUBLIC_BASE_URL` is not configured, the tool falls back to:

```text
media://cache/<file_id>
```

The MCP server registers the matching resource template:

```text
media://cache/{file_id}
```

A client can therefore use `resources/read` as a compatibility mechanism. This is **not** the preferred path for large binary files: MCP binary resource contents are represented as a base64 blob on the wire.

`ResourceLink` itself is the handoff reference; it does not embed the binary payload.

## Existing compatibility tools

These tools remain available so older clients and debugging workflows do not break:

```text
get_cached_file_info
read_cached_file_chunk_base64
get_output_inline_base64
```

They are fallback paths, not the normal delivery mechanism for generated media.

## Client -> MCP

The current generic compatibility imports remain:

```text
cache_file_base64
file_upload_begin
file_upload_chunk
file_upload_finish
file_upload_abort
```

The chunked path prevents one giant JSON request, but its chunks are still base64 and should not be chosen when a client can provide a native file/resource reference through a supported integration path.

Do not claim that arbitrary client-local files can be dereferenced by the MCP server merely because MCP supports `ResourceLink`: the server must actually have a URI it is authorized and able to retrieve, or the client must use a supported upload mechanism.

## Cache -> ComfyUI

`comfy_upload_cached_image(file_id)` performs a server-side multipart upload directly from the MCP cache to ComfyUI `/upload/image`.

It does not convert the image to base64 and does not route the payload through the AI/client context.

This is the preferred path for chains such as:

```text
Blender render / Qwen output / cached image
        ↓
MCP cache file_id
        ↓
comfy_upload_cached_image
        ↓
ComfyUI LoadImage
        ↓
Qwen / LTXV workflow
```

## HTTP authentication

A `ResourceLink` does not grant authorization by itself. `/files/{file_id}` remains behind the deployment's normal authentication/proxy policy. Clients must be able to retrieve the HTTP(S) URI with the appropriate authorization context.

If a client cannot use the authenticated HTTP URI, `media://cache/{file_id}` + MCP `resources/read` remains the protocol-level compatibility fallback.

## Large-file rule

There is deliberately no artificial output-size limit imposed by inline JSON/base64 on the preferred path. Effective limits still come from the client, reverse proxy, filesystem, HTTP stack and deployment policy.

For large outputs, use file references and streaming up to those real platform limits rather than copying the payload into the language-model context.
