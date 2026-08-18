# Third-party components

MCP Video Gen itself is licensed under the Apache License 2.0. The server can install, download, build, or invoke independent third-party components at runtime. Those components remain subject to their own upstream licenses and are not relicensed by this repository.

| Component | Purpose | Integration | Upstream license / note |
|---|---|---|---|
| FFmpeg | Media decoding, encoding, filters, analysis | Debian runtime package | Licensing depends on the FFmpeg build and enabled libraries. |
| HyperFrames | HTML/CSS/media rendering | npm package installed at runtime | See the HyperFrames upstream repository/package for its current license. |
| PySceneDetect | Scene detection | Python dependency | BSD-style upstream license. |
| OpenTimelineIO | Timeline representation | Python dependency | Modified Apache 2.0 upstream license. |
| pysubs2 | Subtitle parsing/editing | Python dependency | MIT upstream license. |
| OpenCV | Lightweight frame analysis | Headless Python dependency | Apache 2.0 upstream license. |
| aubio | Beat/onset/pitch analysis | Debian `aubio-tools` runtime package | GPL-3.0 upstream. Invoked as an external command-line program. |
| RNNoise | Speech denoising | Checksum-pinned upstream source + model built locally into `/data/tooling/rnnoise` | BSD-3-Clause upstream. |
| Silero VAD | Voice activity detection | Checksum-pinned ONNX model downloaded at runtime | See Silero VAD upstream license; model is stored in the persistent data volume. |
| whisper.cpp | Local speech transcription | Source is downloaded/built locally at runtime | MIT upstream. Model files have their own upstream distribution terms. |
| Piper | Optional local TTS | Optional `piper-tts` runtime install; disabled by default | Current Piper runtime is GPL-3.0. Voice models may have separate per-voice licenses. No voice is downloaded automatically by this project. |
| Cloudflared | Optional secure tunnel | Independent sidecar image | See Cloudflare upstream distribution terms. |

The public repository does not include third-party model binaries or voice models. Where the default deployment downloads source/model artifacts automatically, URLs and SHA-256 values are explicit in `video-mcp.yml` / `scripts/prepare_media_tools.sh`, and the resulting artifacts are stored only in the deployment's persistent `/data` volume.

Users redistributing a deployment, container image, bundled model, or derivative work are responsible for complying with the licenses of the third-party components and model/voice assets they choose to include.
