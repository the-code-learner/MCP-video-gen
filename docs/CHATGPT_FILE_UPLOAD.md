# Native ChatGPT file upload

MCP Video Gen uses ChatGPT's native MCP file-parameter contract for files attached to a ChatGPT conversation.

The preferred tools are:

```text
save_uploaded_file(file)
save_uploaded_files(files)
```

Both tools declare `_meta["openai/fileParams"]`. A compatible ChatGPT client binds the selected attachment to that parameter and supplies a temporary authorized file object directly to the MCP tool:

```json
{
  "download_url": "https://...temporary-authorized-url...",
  "file_id": "file_...",
  "mime_type": "audio/mpeg",
  "file_name": "reference.mp3"
}
```

`download_url` and `file_id` are required. `mime_type` and `file_name` are optional.

## Data path

```text
user attaches file in ChatGPT
        |
        v
ChatGPT binds openai/fileParams
        |
        | temporary authorized HTTPS download object
        v
save_uploaded_file
        |
        | server-side streaming, 64 KiB reads
        v
Video Gen persistent cache
        |
        v
canonical file_id
        |
        +--> comfy_upload_cached_media
        +--> FFmpeg / analysis / transcription
        +--> Blender
        +--> HyperFrames
```

The file bytes do not need to be copied into a tool argument as Base64 and do not need model-mediated chunk orchestration.

## Why the old binary upload tools were removed

Earlier releases exposed `cache_file_base64` plus `file_upload_begin`, `file_upload_chunk[_auto]`, `file_upload_status`, `file_upload_finish` and `file_upload_abort`. Those compatibility paths required binary Base64 to cross tool JSON and, when Programmatic Tool Calling was unavailable, could require a language-model turn between chunks.

They are intentionally removed in v3.0.0. For ChatGPT attachments the native file-param route is both simpler and more autonomous. For generic remote artifacts that already have a real retrievable HTTPS URL, `import_remote_file(uri)` remains available.

`cache_text_file` is retained because it stores UTF-8 text authored by the model/client rather than transporting attached binary media. `read_cached_file_chunk_base64` is retained only as an outbound compatibility/debug fallback.

## Server-side validation

For each native ChatGPT file, Video Gen:

- accepts only HTTPS URLs;
- rejects embedded URL credentials;
- accepts only the normal HTTPS port (443);
- resolves the hostname and rejects non-public/non-global addresses;
- revalidates every redirect;
- bounds redirects and timeout;
- rejects a declared `Content-Length` above `MAX_UPLOAD_MB`;
- also enforces `MAX_UPLOAD_MB` while streaming, so a missing/incorrect Content-Length cannot bypass the limit;
- computes SHA-256 while streaming;
- writes first to a temporary `.part` file and only promotes a completed transfer to the cache;
- stores the final artifact through the normal canonical `file_id` cache contract.

Defaults are:

```text
CHATGPT_FILE_TIMEOUT_SEC=300
CHATGPT_FILE_MAX_REDIRECTS=5
CHATGPT_FILE_MAX_BATCH_FILES=20
```

The timeout is bounded to 1800 seconds, redirect count to 10, and batch count to 100. `MAX_UPLOAD_MB` remains the per-file byte limit.

## Temporary URL secrecy

Temporary ChatGPT download URLs are bearer-like capabilities. They must not be copied into prompts, generated workflows, memory, user-facing output, or persistent logs.

The activity audit already strips HTTP(S) query strings before persistence. The native upload module also raises the generic `httpx`/`httpcore` request loggers above INFO because those libraries otherwise log full request URLs, including signed query strings.

Only non-secret source metadata such as the source host, client file ID, MIME type, computed SHA-256 and final cache metadata is retained.

## Generic HTTPS files

When an artifact is not a ChatGPT attachment but does have a real HTTPS URL reachable by Video Gen, use:

```text
import_remote_file(uri, filename="", expected_size_bytes=0, expected_sha256="")
```

That path remains useful for public object storage, another service's intentionally retrievable file URL, or other server-to-server transfers. Do not invent a URL from an opaque file ID.

## After upload

Always reuse the returned Video Gen `file_id`.

For ComfyUI:

```text
save_uploaded_file
        ↓
file_id
        ↓
comfy_upload_cached_media(file_id)
        ↓
workflow_input_value
```

For a standard image `LoadImage`, use the returned `workflow_load_image_value`. For audio/video/custom loaders, inspect the installed ComfyUI node definition and use `workflow_input_value` only where that node actually accepts an input filename/path.

## Manual WebGUI upload

The WebGUI browser upload remains available for administration, recovery and debugging. It is not the preferred autonomous ChatGPT path.
