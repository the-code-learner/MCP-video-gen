# MCP build status

`build_status()` is the canonical MCP-visible way to verify which MCP Video Gen build a connected client is actually talking to.

Use it after a deployment/restart or whenever a client may have cached an older MCP schema:

```text
build_status()
```

The result includes:

- `server`: MCP server name;
- `app_version`: application version exported from the running release's `VERSION` file;
- `source_ref`: immutable bootstrap source ref from `.mcp-source-ready` (for example `v2.6.1` or a pinned commit SHA);
- `python_version`;
- `mcp_sdk_version`;
- `tool_count`: number of tools currently registered in the running MCP process;
- safe feature flags;
- configured transfer/runtime limits;
- selected non-secret runtime booleans and mount-presence checks;
- the canonical ComfyUI file-routing reminder.

Example shape:

```json
{
  "server": "video-mcp",
  "app_version": "2.6.1",
  "source_ref": "v2.6.1",
  "python_version": "3.11.x",
  "mcp_sdk_version": "2.0.0",
  "tool_count": 99,
  "features": {
    "native_file_handoff": true,
    "remote_file_ingress": true,
    "file_transfer_guide": true,
    "comfy_cache_image_upload": true,
    "workflow_loadimage_guard": true
  },
  "limits": {
    "max_upload_mb": 32,
    "max_inline_output_mb": 8
  }
}
```

The exact tool count can grow in later releases; compare it with the version/source ref rather than treating `99` as a permanent constant.

## Security

`build_status` intentionally does **not** return secrets or sensitive values. In particular it does not expose:

- Blender bridge tokens;
- Cloudflare Access audience values;
- Cloudflare/tunnel credentials;
- signed file URLs;
- file contents;
- secret environment-variable values;
- the configured public base URL itself.

It only reports safe booleans such as whether a public base URL or remote-import allowlist is configured.

## Why both version and source ref?

`app_version` answers "what application version does this source declare?".

`source_ref` answers "which immutable release/tag/commit did the Portainer bootstrap actually select?".

For a normal stable deployment they should agree conceptually, for example:

```text
app_version = 2.6.1
source_ref  = v2.6.1
```

If a deployment is intentionally pinned to a commit SHA, `source_ref` can instead be that commit while `app_version` remains the version declared by that source tree.

This makes `build_status` more useful than checking only `/health`: it verifies the identity of the MCP instance through the same MCP connection the AI/client is actually using.
