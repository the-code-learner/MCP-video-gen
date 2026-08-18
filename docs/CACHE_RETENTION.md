# Persistent cache retention

MCP Video Gen stores imported and generated artifacts in the persistent media cache under `/data/exports`. Historically those files remained indefinitely. Version 2.7.0 keeps that behavior as the default and adds an **opt-in** retention policy.

## Backward-compatible default

If the retention variables are absent, or if `CACHE_CLEANUP_ENABLED` is false, the MCP does not start an automatic cleanup worker and files continue to persist across restarts/redeploys as before.

Equivalent defaults are:

```text
CACHE_CLEANUP_ENABLED=false
CACHE_RETENTION_DAYS=0
CACHE_MAX_SIZE_GB=0
CACHE_CLEANUP_INTERVAL_HOURS=24
```

`CACHE_RETENTION_DAYS=0` disables age-based deletion. `CACHE_MAX_SIZE_GB=0` disables size-based deletion. The interval only matters when automatic cleanup is enabled.

## Policy behavior

When automatic cleanup is explicitly enabled, the MCP runs one maintenance pass after startup and then repeats it at the configured interval.

The cleanup order is:

1. remove unpinned files older than `CACHE_RETENTION_DAYS` when that value is greater than zero;
2. if the remaining unpinned+ pinned cache is still above `CACHE_MAX_SIZE_GB`, remove the oldest remaining **unpinned** files until the cache reaches the limit or no unpinned files remain;
3. pinned files are never selected by either rule.

If pinned files alone exceed the configured size limit, the MCP leaves them untouched. The cache can therefore remain above the configured limit until pinned artifacts are explicitly unpinned or removed by an administrator.

Automatic cleanup is maintenance only: a cleanup failure does not terminate the MCP process. The next interval retries.

## MCP tools

### `cache_status()`

Reports:

- whether automatic cleanup is enabled;
- retention days, maximum size and interval;
- current file count and total bytes/GiB;
- pinned/unpinned counts;
- oldest/newest cached creation timestamps;
- how many bytes/files would currently be eligible for deletion;
- whether the automatic worker is running.

Use this before assuming that old artifacts are automatically deleted.

### `cache_cleanup(dry_run=true)`

Applies the configured age/size policy. It defaults to `dry_run=true` and returns the candidate set without deleting anything. Use `dry_run=false` only after reviewing the preview.

This tool can be called even when automatic cleanup is disabled, but it still uses the configured `CACHE_RETENTION_DAYS` / `CACHE_MAX_SIZE_GB` rules. If both are zero, there are no candidates.

### `cache_pin(file_id, note="")`

Persists a retention pin in the file's cache metadata. Use it for long-lived project references, `.blend` scenes, final masters, source images or any artifact that must not be deleted by retention maintenance.

### `cache_unpin(file_id)`

Removes retention protection. The artifact becomes eligible for the next age/size cleanup pass.

## Metadata

Pins are stored in the existing `<file_id>.json` cache metadata under a `retention` object. Pin state therefore survives container recreation and normal MCP updates because `/data` is persistent.

## Scope

The retention policy applies to canonical media-cache artifacts under `/data/exports`. It does not automatically remove:

- HyperFrames projects;
- timelines;
- local models/tooling;
- Piper voices;
- ComfyUI's own input/output directories;
- source-release or Python-venv caches.

Those stores have separate lifecycle concerns and are intentionally not coupled to media retention.
