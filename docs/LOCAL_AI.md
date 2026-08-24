# Local AI, voice cloning, music and resource management

MCP Video Gen v3.1 adds optional local AI/audio capabilities without making large model downloads part of normal container startup.

## Design goals

- Inference stays local for STT/TTS/voice cloning.
- Large artifacts are opt-in. Any managed model family with artifacts above 100 MiB must expose at least `light` and `optimal` choices and neither is preloaded.
- `/data` remains the persistent home for models, cloned voices and generated media.
- Qwen3-TTS is isolated from the main MCP Python environment and runs as a Video Gen-owned child worker only when needed.
- GPU access is shared. Video Gen does not reserve a GPU exclusively and never manages ComfyUI, Blender or another container's processes.
- Cache deletion and model/voice removal are explicit destructive actions and require confirmation.

## Model catalog

Use `model_catalog()` to inspect current choices and installation state, then `model_recommend(family)` to obtain a point-in-time recommendation based on disk, RAM and VRAM.

Status checks use verified installation markers so routine inventory does not hash several GiB on every request. `model_verify(family, profile)` performs an explicit full SHA-256 integrity pass when a deep verification is wanted.

### Whisper

The historical `tiny-q5_1` model remains the small bootstrap/fallback model. Optional profiles are:

- `light`: `ggml-small-q5_1.bin`, 190,085,487 bytes.
- `optimal`: `ggml-large-v3-turbo-q5_0.bin`, 574,041,195 bytes.

Optional models are downloaded only through `model_install("whisper", profile, confirm=true)`, checked for expected byte size and SHA-256, and recorded with a verified installation marker. `model_select()` switches `/data/models/whisper/selected.bin` to the chosen installed model without requiring an MCP restart.

### Qwen3-TTS

Qwen3-TTS uses an isolated environment under `/data/tooling/qwen3-tts/venv`; its models live under `/data/qwen3-tts/models`.

- `light`: `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, approximately 2.52 GB.
- `optimal`: `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, approximately 4.54 GB.

The common Qwen runtime is installed only when the first Qwen model installation is explicitly confirmed. Pip caching is disabled for this isolated runtime so installation does not leave a second multi-GiB wheel cache under `/data`.

Model snapshots use full 40-character Hugging Face commit pins. Installation verifies both the main model weights and the large speech-tokenizer weights with SHA-256 before writing the installation marker. A failed verification removes the incomplete model directory.

`model_recommend("qwen3-tts")` includes estimated common runtime overhead when it is not installed yet.

## Voice cloning

Persistent cloned voices are stored under `/data/qwen3-tts/voices/<name>` and contain a normalized local reference WAV plus profile metadata.

Create a profile with:

```text
qwen_voice_clone_create(
  file_id=<cached reference audio>,
  name="speaker-name",
  reference_text="exact text spoken in the reference",
  language="it" | "en",
  consent_confirmed=true
)
```

`consent_confirmed=true` is mandatory. It must only be supplied when the user has explicitly indicated that cloning the reference voice is authorized.

Use:

- `qwen_voice_clone_list()`
- `qwen_voice_clone_info(name)`
- `qwen_voice_clone_delete(name, confirm=true)`
- `qwen_tts_synthesize(text, voice, language, model_profile, output_filename)`

The initial product contract is intentionally limited to Italian and English. Qwen itself may support additional languages, but this MCP interface does not silently enable them.

The child worker bounds `max_new_tokens` (4096 by default, configurable with `QWEN_TTS_MAX_NEW_TOKENS`) so a synthesis that fails to emit an end token cannot grow without a limit.

## Unified TTS and Piper

`tts_generate()` provides a stable local TTS entry point:

- `engine="qwen3"` uses an installed Qwen profile and a persistent cloned voice.
- `engine="piper"` uses an installed Piper `.onnx` voice.

The historical `tts_local()` and `piper_import_voice_file()` remain available for backward compatibility.

Convenience management tools are:

- `piper_voice_catalog(language="it" | "en")`
- `piper_voice_install(model_url, config_url, voice_name, confirm=true, ...)`
- `piper_voice_remove(voice_name, confirm=true)`

The curated catalog initially exposes Italian `it_IT-paola-medium` and English `en_US-lessac-medium`. Both ONNX artifacts are below 100 MiB and therefore do not need the managed large-model light/optimal pair. The convenience installer is capped at 100 MiB so it cannot be used to bypass the large-artifact policy. Model SHA-256 values are provided by the catalog; per-voice/model-card licensing remains the user's responsibility.

Piper remains disabled by default at deployment level because it is an optional runtime with its own licensing considerations.

## Openverse music

`music_search()` queries the public Openverse audio endpoint. No audio is downloaded during search.

For `commercial_use=true`, Video Gen asks Openverse for commercial-use results and additionally restricts returned license codes to the conservative low-friction set used by this integration (`cc0`, `pdm`, `by`). This is a filtering aid, not a legal guarantee.

`duration_min_sec` / `duration_max_sec` are exact user-facing second bounds. Openverse returns audio `duration` in milliseconds and its own `length` query parameter is categorical rather than a numeric range, so Video Gen applies exact duration filtering locally across a bounded number of search pages.

Search results retain title, creator, original landing page, provider/source, license/version/license URL, attribution and duration metadata.

`music_import(result_id)` downloads a result selected from a recent search into the normal Video Gen media cache through the verified public-HTTPS downloader and persists attribution/license metadata alongside the cached artifact.

Openverse aggregates metadata from other sources. Always verify the original source landing page and license before publication when licensing is material to the project.

## Storage and recommendations

`storage_info()` reports `total`, `used` and `free` space for the filesystem backing `/data`. Because `/data` is a Docker named volume, this is the relevant filesystem for persistent Video Gen models/cache; it is not necessarily every disk attached to the VM.

With breakdown enabled, it also estimates usage for:

- media cache (`/data/exports`)
- models
- tooling/runtimes
- Piper
- Qwen
- HyperFrames
- timelines
- temporary files

`model_recommend()` reserves 2 GiB of installation headroom in addition to the estimated new model/runtime footprint.

If disk headroom is insufficient, the recommendation may include a `cache_reclaim_preview`. This is informational only.

## Cache reclamation and approval

`cache_reclaim_preview(required_gib, reserve_gib)` selects the oldest unpinned Video Gen cache files until the requested reclaim target is met. It never deletes anything.

Actual deletion requires both an explicit list of `file_id` values and `confirm=true`:

```text
cache_reclaim_files(file_ids=[...], confirm=true)
```

The client must show the preview to the user and obtain approval before this call. Pinned files are rejected even if their IDs are supplied explicitly.

This flow is independent from the existing optional automatic retention policy.

## RAM attribution

`runtime_resources()` reads the current container memory cgroup where available:

- cgroup v2: `memory.current` / `memory.max`
- cgroup v1 fallback: `memory.usage_in_bytes` / limit

This gives reliable cgroup-accounted memory for Video Gen and its child processes without exposing Docker socket or host PID namespace.

`/proc/meminfo` supplies VM-visible total/available RAM. The reported `external_or_shared_estimate` is the remainder after subtracting cgroup-accounted Video Gen memory from host-visible used memory. It is intentionally labelled an estimate because kernel memory, page cache and shared pages prevent perfect per-container host attribution from this security boundary.

## GPU and VRAM attribution

The Compose service receives one shared NVIDIA GPU with `compute,utility` driver capabilities. This does **not** make the device exclusive; other containers may use it concurrently.

Global GPU total/used/free memory is read with NVIDIA tooling. The isolated Qwen worker reports its own PyTorch allocated/reserved memory and, when one GPU is visible, the visible GPU UUID. Video Gen matches that UUID to NVIDIA telemetry; the supported single-visible-GPU deployment also has a safe fallback that does not assume the physical GPU index is zero.

Only the registered worker's allocator usage is attributed to Video Gen. The remainder is reported as `external_or_unattributed_estimate`; it may belong to ComfyUI, Blender, another container/process, or CUDA/driver/context overhead.

Video Gen intentionally does not expose `pid: host` and does not mount `/var/run/docker.sock`, so it does not claim a reliable name/PID mapping for external GPU consumers.

## Freeing RAM/VRAM safely

`release_runtime_resources(aggressive=false)` asks the registered Qwen worker to unload its model, run garbage collection and clear its CUDA allocator cache while keeping the worker and MCP container running.

`release_runtime_resources(aggressive=true)` additionally shuts down only the exact Qwen child process created and held by Video Gen's `Popen` registry. It is recreated on demand during the next synthesis.

The implementation never uses `killall`, `pkill`, GPU reset, Docker APIs or arbitrary PIDs discovered through `nvidia-smi`. It cannot terminate ComfyUI, Blender, another container, or an unrelated host process through this feature.

Worker stderr is continuously drained into a bounded in-memory diagnostic tail so a verbose Torch/Qwen process cannot deadlock on a full stderr pipe.

## Read-only WebGUI system page

When the WebGUI is enabled, `/system` shows read-only storage, RAM, GPU/VRAM, Qwen worker and optional-model status. `/api/system` exposes the same safe telemetry to the page.

Installation, removal, resource release and cache reclamation are intentionally **not** exposed as destructive WebGUI buttons in v3.1; those actions remain explicit MCP tool calls with their confirmation contracts.

## GPU-less operation

GPU telemetry may report unavailable and Qwen can still be installed for CPU fallback. Recommendations prefer the lighter profile when GPU/VRAM headroom is absent or constrained. Existing FFmpeg, Whisper, Piper, ComfyUI routing, Blender and other Video Gen features remain independently available according to their normal runtime configuration.
