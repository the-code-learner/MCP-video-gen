# WebGUI and activity audit

MCP Video Gen v2.8.0 includes a lightweight same-process WebGUI at `/`.
It does not require a separate frontend container, Node build, database service,
or additional public port.

## Cache tab

The Cache tab reads the persistent MCP cache and shows filename, `file_id`, MIME
type, size, creation time, source and retention pin state. Images, audio and video
use the existing authenticated `/files/{file_id}` route for previews/downloads.

The browser can upload a file directly into the MCP cache. Uploads are streamed to
a temporary file, bounded by `MAX_UPLOAD_MB`, hashed while streaming and then moved
into `/data/exports`. The browser never converts the upload to base64.

Pinned files are protected from automatic retention cleanup. The WebGUI also blocks
ordinary deletion of a pinned file unless the user explicitly confirms the forced
delete path.

## Activity tab

The Activity tab uses a bounded SQLite database at:

```text
/data/audit/events.sqlite3
```

The MCP SDK server middleware records inbound MCP methods, including `tools/call`,
plus tool name, status, duration and bounded argument/result summaries. WebGUI cache
mutations are written into the same activity stream with `source=webgui`.

Audit sanitization intentionally:

- redacts secret-like keys such as tokens, passwords, authorization values and API keys;
- removes query strings/fragments from HTTP(S) URLs so signed URL tokens are not persisted;
- replaces long binary/base64-like strings with a redaction marker;
- truncates large/deep structures rather than persisting arbitrary workflow payloads.

The audit is operational diagnostics, not a byte-for-byte request archive.

## Configuration

```text
WEBGUI_ENABLED=true
AUDIT_LOG_ENABLED=true
AUDIT_RETENTION_DAYS=30
AUDIT_MAX_ROWS=20000
```

`WEBGUI_ENABLED=false` omits `/` and the dashboard `/api/*` routes while keeping the
MCP endpoint and `/files/{file_id}` unchanged.

`AUDIT_LOG_ENABLED=false` disables persistent activity recording. A retention value
of `0` disables age-based audit deletion; `AUDIT_MAX_ROWS` remains a hard bounded-history
control (minimum 100 rows).

## Security

The WebGUI is placed in the outer Starlette application, before the catch-all MCP
mount, so it remains behind the same deployment authentication boundary as the file
routes. `/health` keeps its existing special-case behavior.

State-changing dashboard calls additionally require the `X-MCP-WebGUI: 1` custom
header. The project does not enable cross-origin browser access, so a normal cross-site
HTML form cannot perform upload/pin/unpin/delete operations.

Do not expose the WebGUI on a separate unauthenticated origin merely for convenience.
