# Programmatic chunked upload

MCP Video Gen exposes a deterministic chunked-upload contract that is suitable for
Programmatic Tool Calling (PTC) when the **client** supports it.

PTC is not an MCP-server feature that this project can enable by itself. OpenAI's
GPT-5.6 guidance describes PTC as a Responses API capability: the application adds
`programmatic_tool_calling`, opts eligible tools in with `allowed_callers`, and
handles `program`, program-issued tool calls, and `program_output` while preserving
call/caller linkage.

Therefore this server does two things:

1. exposes predictable upload tools/result fields that are safe to call in a loop;
2. publishes instructions telling PTC-capable clients exactly which bounded stage
   should be programmatic.

A ChatGPT/MCP client that does not expose PTC, or a program runtime that cannot access
the source file bytes, must use the same tools through ordinary direct tool calls.
PTC does **not** magically make a ChatGPT attachment readable by a remote MCP server.

## Preferred routing

Use this order:

1. `import_remote_file(uri)` when Video Gen can retrieve a real HTTPS URL directly;
2. one `cache_file_base64(filename, data_base64)` call when the complete payload fits;
3. if one-shot is rejected/truncated, one chunked session using the auto-offset tools.

After any successful import, keep the returned `file_id` canonical. Do not re-encode
or re-upload the same unchanged artifact.

## PTC bounded stage

When the client supports PTC **and the program can access the source bytes**, the
bounded stage is:

```text
file_upload_begin
        ↓
loop over deterministic source byte ranges
        ↓
file_upload_chunk_auto
        ↓
(stop when complete_by_size=true)
        ↓
file_upload_finish
        ↓
canonical file_id
```

Optional recovery uses `file_upload_status(upload_id)`.

The program should not return to the language model between successful chunks. It
should use only documented fields and stop if the source bytes become ambiguous.

## `file_upload_chunk_auto`

Unlike the legacy `file_upload_chunk`, the preferred tool has no `offset_bytes`
argument:

```text
file_upload_chunk_auto(
    upload_id,
    data_base64,
    expected_decoded_bytes=0,
)
```

The server's `.part` size is authoritative. Before appending, the server validates:

- base64 syntax;
- optional exact decoded length;
- 4 MiB decoded per-call ceiling;
- configured `MAX_UPLOAD_MB`;
- `expected_size_bytes` from `file_upload_begin`.

A rejected chunk returns:

```json
{
  "accepted": false,
  "file_unchanged": true,
  "reason": "decoded_size_mismatch",
  "total_received_bytes": 24000,
  "remaining_bytes": 93175,
  "next_action": "retry_same_chunk"
}
```

The rejected payload is **not appended** and server progress does not advance.

An accepted chunk returns fields including:

```json
{
  "accepted": true,
  "received_bytes": 12000,
  "total_received_bytes": 36000,
  "remaining_bytes": 81175,
  "complete_by_size": false,
  "next_action": "send_next_chunk_with_file_upload_chunk_auto"
}
```

When `complete_by_size=true`, call `file_upload_finish` immediately. Finish verifies
exact total size and, when supplied, SHA-256 before promoting the temporary upload to
the persistent cache.

## Client-side orchestration contract

A PTC-capable client should constrain the programmatic stage to only the upload tools
needed for this operation. The intended orchestration is conceptually:

```javascript
const session = await file_upload_begin({
  filename,
  expected_size_bytes: source.length,
  expected_sha256: sha256
});

for (const rawChunk of deterministicChunks(source)) {
  const result = await file_upload_chunk_auto({
    upload_id: session.upload_id,
    data_base64: toBase64(rawChunk),
    expected_decoded_bytes: rawChunk.length
  });

  if (!result.accepted) {
    return {ok: false, stage: "chunk", reason: result.reason};
  }
}

const final = await file_upload_finish({upload_id: session.upload_id});
return {ok: true, file_id: final.file_id, size_bytes: final.size_bytes};
```

This snippet describes the desired program logic; the actual tool-call syntax inside
a hosted PTC runtime is controlled by the client/OpenAI API integration.

## How to test whether a client is actually using PTC

Use a file large enough that one-shot base64 is known to fail for that client, then
request the PTC upload path. In the Activity log, inspect the timestamps for
`file_upload_begin`, consecutive `file_upload_chunk_auto` calls, and
`file_upload_finish`.

If chunks arrive back-to-back with little or no model-scale gap, the client is likely
executing the deterministic loop programmatically. If there are tens of seconds
between each chunk and repeated discovery/list calls, the client is still doing
model-mediated orchestration.

Always verify the final size and SHA-256 rather than inferring success from HTTP/MCP
transport status alone.
