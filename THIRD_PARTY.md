# Third-party components

MCP Video Gen itself is licensed under the Apache License 2.0. The server can install, download, build, or invoke independent third-party components at runtime. Those components remain subject to their own upstream licenses and are not relicensed by this repository.

| Component | Purpose | Integration | Upstream license / note |
|---|---|---|---|
| FFmpeg | Media decoding, encoding, filters, analysis | Debian runtime package | Licensing depends on the FFmpeg build and enabled libraries. |
| SoX | Audio utility dependency for optional Qwen TTS runtime | Debian runtime package | The SoX executable is GPL-licensed; libsox is available under GPL/LGPL terms and some optional components are GPL-only. This deployment installs Debian packages and does not relicense them. |
| HyperFrames | HTML/CSS/media rendering | npm package installed at runtime | See the HyperFrames upstream repository/package for its current license. |
| PySceneDetect | Scene detection | Python dependency | BSD-style upstream license. |
| OpenTimelineIO | Timeline representation | Python dependency | Modified Apache 2.0 upstream license. |
| pysubs2 | Subtitle parsing/editing | Python dependency | MIT upstream license. |
| OpenCV | Lightweight frame analysis | Headless Python dependency | Apache 2.0 upstream license. |
| aubio | Beat/onset/pitch analysis | Debian `aubio-tools` runtime package | GPL-3.0 upstream. Invoked as an external command-line program. |
| RNNoise | Speech denoising | Checksum-pinned upstream source + model built locally into `/data/tooling/rnnoise` | BSD-3-Clause upstream. |
| Silero VAD | Voice activity detection | Checksum-pinned ONNX model downloaded at runtime | See Silero VAD upstream license; model is stored in the persistent data volume. |
| whisper.cpp | Local speech transcription | Source is downloaded/built locally at runtime | MIT upstream. Model files have their own upstream distribution terms. The optional v3.1 light/optimal models are never preloaded and are size/SHA-256 verified on install. |
| Qwen3-TTS / `qwen-tts` | Optional local TTS and voice cloning | Isolated Python venv/child worker installed on demand under `/data/tooling/qwen3-tts`; model snapshots under `/data/qwen3-tts/models` | Qwen3-TTS model repositories and the pinned `qwen-tts` runtime package are Apache-2.0. Model/runtime downloads occur only after explicit model installation. Full snapshot revisions are pinned and the main model plus speech-tokenizer weights are SHA-256 verified before installation is accepted. |
| Piper / `piper-tts` | Lightweight local TTS | Pinned Python runtime dependency `piper-tts==1.6.0`; voices remain separately installed under `/data/piper/voices` | Piper runtime is GPL-3.0-or-later. The historical deployment enable/disable setting remains backward compatible; a user-confirmed persistent state under `/data/piper/runtime-enabled` can enable or disable Piper without a YAML change. Voice models may have separate per-voice/dataset licenses; no voice is downloaded automatically. |
| Openverse | Open-license audio/music discovery | Public Openverse audio API queried at runtime; selected media is downloaded into the MCP cache | Openverse aggregates media and license metadata from upstream providers. Each audio work retains its own license/attribution; users should verify the original landing page before publication. |
| NVIDIA driver/runtime | Optional shared GPU compute and NVIDIA telemetry | Supplied by the Docker host/NVIDIA Container Toolkit; not bundled in this repository | Subject to NVIDIA's applicable driver/toolkit license terms. Video Gen does not make the GPU exclusive and does not receive host PID/Docker-socket access. |
| Blender | Optional 3D creation/rendering backend | Installed independently on the host VM and invoked only through the optional local bridge; Blender is not bundled by this repository | Blender is GPL-licensed upstream. Files created with Blender are not automatically subject to Blender's GPL merely because Blender produced them; users remain responsible for third-party assets/add-ons they include. |
| Cloudflared | Optional secure tunnel | Independent sidecar image | See Cloudflare upstream distribution terms. |

The public repository does not include Blender, Qwen model weights, optional large Whisper models, third-party voice models, downloaded music, or other user-selected media/model assets. Where the default deployment downloads source/model artifacts automatically, URLs and SHA-256 values are explicit in `video-mcp.yml` / `scripts/prepare_media_tools.sh`, and the resulting artifacts are stored only in the deployment's persistent `/data` volume.

The v3.1 optional model registry intentionally separates large downloadable artifacts from bootstrap: any registered family with artifacts above 100 MiB must expose at least `light` and `optimal` choices, and CI enforces that those choices are marked non-preloaded. Qwen snapshot revisions use full commit pins; direct Whisper model downloads are size- and SHA-256-verified. Routine status uses installation markers, while `model_verify()` can perform a full integrity pass later.

The Blender bridge code in this repository is part of MCP Video Gen and remains Apache-2.0; it communicates with a separately installed Blender executable rather than redistributing Blender itself.

Users redistributing a deployment, container image, bundled model, Blender add-on/asset, voice clone, downloaded music file, or derivative work are responsible for complying with the licenses/permissions of the third-party components and model/voice/asset files they choose to include.
