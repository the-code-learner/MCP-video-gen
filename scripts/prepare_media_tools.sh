#!/bin/sh
set -eu

DATA_ROOT="${VIDEO_MCP_DATA_ROOT:-/data}"
VENV_DIR="${VIDEO_MCP_VENV_DIR:-/opt/venv}"

is_true() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

download_verified() {
  url="$1"
  sha256="$2"
  destination="$3"
  mkdir -p "$(dirname "$destination")"
  if [ -f "$destination" ]; then
    current="$(sha256sum "$destination" | awk '{print $1}')"
    if [ "$current" = "$sha256" ]; then
      return 0
    fi
    echo "Checksum mismatch for cached asset $destination; refreshing." >&2
    rm -f "$destination"
  fi
  tmpfile="${destination}.part.$$"
  rm -f "$tmpfile"
  if ! curl -fL --retry 5 --retry-delay 3 --connect-timeout 20 -o "$tmpfile" "$url"; then
    rm -f "$tmpfile"
    return 1
  fi
  actual="$(sha256sum "$tmpfile" | awk '{print $1}')"
  if [ "$actual" != "$sha256" ]; then
    echo "SHA-256 mismatch for $url" >&2
    echo "expected: $sha256" >&2
    echo "actual:   $actual" >&2
    rm -f "$tmpfile"
    return 1
  fi
  mv "$tmpfile" "$destination"
}

mkdir -p \
  "$DATA_ROOT/models/silero-vad" \
  "$DATA_ROOT/models/whisper" \
  "$DATA_ROOT/tooling/whisper.cpp" \
  "$DATA_ROOT/tooling/rnnoise" \
  "$DATA_ROOT/piper/voices" \
  "$DATA_ROOT/timelines"

if is_true "${SILERO_VAD_ENABLED:-true}"; then
  SILERO_DEFAULT_URL="https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx"
  SILERO_DEFAULT_SHA="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
  SILERO_LEGACY_BAD_SHA="2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
  SILERO_URL="${SILERO_VAD_MODEL_URL:-$SILERO_DEFAULT_URL}"
  SILERO_SHA="${SILERO_VAD_MODEL_SHA256:-$SILERO_DEFAULT_SHA}"
  SILERO_PATH="${SILERO_VAD_MODEL_PATH:-$DATA_ROOT/models/silero-vad/silero_vad.onnx}"

  # v2.4.2 shipped an incorrect default SHA in the Portainer YAML. Preserve
  # compatibility with already-deployed stacks while keeping checksum
  # verification strict for custom URLs and custom pins.
  if [ "$SILERO_URL" = "$SILERO_DEFAULT_URL" ] && [ "$SILERO_SHA" = "$SILERO_LEGACY_BAD_SHA" ]; then
    echo "Replacing legacy incorrect Silero VAD v6.2.1 checksum pin." >&2
    SILERO_SHA="$SILERO_DEFAULT_SHA"
  fi

  download_verified "$SILERO_URL" "$SILERO_SHA" "$SILERO_PATH"
fi

if is_true "${RNNOISE_ENABLED:-true}"; then
  RNNOISE_REF="${RNNOISE_REF:-372f7b4b76cde4ca1ec4605353dd17898a99de38}"
  RNNOISE_SOURCE_URL="${RNNOISE_SOURCE_URL:-https://github.com/xiph/rnnoise/archive/$RNNOISE_REF.tar.gz}"
  RNNOISE_SOURCE_SHA="${RNNOISE_SOURCE_SHA256:-40ff1568af151959933699fcbf2db3ee3c62fa9559557dbf61a65e7a12cd335d}"
  RNNOISE_MODEL_VERSION="${RNNOISE_MODEL_VERSION:-0b50c45}"
  RNNOISE_MODEL_URL="${RNNOISE_MODEL_URL:-https://media.xiph.org/rnnoise/models/rnnoise_data-$RNNOISE_MODEL_VERSION.tar.gz}"
  RNNOISE_MODEL_SHA="${RNNOISE_MODEL_SHA256:-4ac81c5c0884ec4bd5907026aaae16209b7b76cd9d7f71af582094a2f98f4b43}"
  RNNOISE_ROOT="$DATA_ROOT/tooling/rnnoise"
  RNNOISE_TARGET="$RNNOISE_ROOT/$RNNOISE_REF"

  if [ ! -f "$RNNOISE_TARGET/install/usr/local/lib/librnnoise.so.0" ]; then
    STAGING="$RNNOISE_ROOT/.staging-$RNNOISE_REF-$$"
    SOURCE_ARCHIVE="$RNNOISE_ROOT/.source-$RNNOISE_REF.tar.gz"
    MODEL_ARCHIVE="$RNNOISE_ROOT/.model-$RNNOISE_MODEL_VERSION.tar.gz"
    rm -rf "$STAGING"
    mkdir -p "$STAGING"
    trap 'rm -rf "$STAGING"' INT TERM HUP EXIT

    download_verified "$RNNOISE_SOURCE_URL" "$RNNOISE_SOURCE_SHA" "$SOURCE_ARCHIVE"
    download_verified "$RNNOISE_MODEL_URL" "$RNNOISE_MODEL_SHA" "$MODEL_ARCHIVE"
    tar -xzf "$SOURCE_ARCHIVE" --strip-components=1 -C "$STAGING"
    cp "$MODEL_ARCHIVE" "$STAGING/rnnoise_data-$RNNOISE_MODEL_VERSION.tar.gz"

    (
      cd "$STAGING"
      ./autogen.sh
      ./configure --prefix=/usr/local --disable-static
      make -j "${RNNOISE_BUILD_JOBS:-2}"
      make DESTDIR="$STAGING/install" install
    )
    test -f "$STAGING/install/usr/local/lib/librnnoise.so.0"
    rm -rf "$RNNOISE_TARGET"
    mv "$STAGING" "$RNNOISE_TARGET"
    trap - INT TERM HUP EXIT
  fi
  ln -sfn "$RNNOISE_TARGET" "$RNNOISE_ROOT/current"
fi

if is_true "${WHISPER_CPP_ENABLED:-true}"; then
  WHISPER_REF="${WHISPER_CPP_REF:-v1.8.6}"
  TOOL_ROOT="$DATA_ROOT/tooling/whisper.cpp"
  TARGET="$TOOL_ROOT/$WHISPER_REF"
  BINARY="$TARGET/build/bin/whisper-cli"

  if [ ! -x "$BINARY" ]; then
    STAGING="$TOOL_ROOT/.staging-$WHISPER_REF-$$"
    ARCHIVE="$TOOL_ROOT/.source-$WHISPER_REF-$$.tar.gz"
    rm -rf "$STAGING"
    mkdir -p "$STAGING"
    trap 'rm -rf "$STAGING" "$ARCHIVE"' INT TERM HUP EXIT
    curl -fL --retry 5 --retry-delay 3 --connect-timeout 20 \
      -o "$ARCHIVE" \
      "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/$WHISPER_REF"
    tar -xzf "$ARCHIVE" --strip-components=1 -C "$STAGING"
    rm -f "$ARCHIVE"
    cmake -S "$STAGING" -B "$STAGING/build" \
      -DCMAKE_BUILD_TYPE=Release \
      -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=ON
    cmake --build "$STAGING/build" --config Release -j "${WHISPER_CPP_BUILD_JOBS:-2}"
    test -x "$STAGING/build/bin/whisper-cli"
    rm -rf "$TARGET"
    mv "$STAGING" "$TARGET"
    trap - INT TERM HUP EXIT
  fi
  ln -sfn "$TARGET" "$TOOL_ROOT/current"

  if is_true "${WHISPER_MODEL_AUTO_DOWNLOAD:-true}"; then
    MODEL_NAME="${WHISPER_MODEL_NAME:-tiny-q5_1}"
    MODEL_URL="${WHISPER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-q5_1.bin}"
    MODEL_SHA="${WHISPER_MODEL_SHA256:-818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7}"
    MODEL_PATH="${WHISPER_MODEL_PATH:-$DATA_ROOT/models/whisper/ggml-$MODEL_NAME.bin}"
    download_verified "$MODEL_URL" "$MODEL_SHA" "$MODEL_PATH"
  fi
fi

if is_true "${PIPER_ENABLED:-false}"; then
  PIPER_SPEC="${PIPER_PACKAGE_SPEC:-piper-tts}"
  MARKER="$VENV_DIR/.piper-package-spec"
  INSTALLED_SPEC="$(cat "$MARKER" 2>/dev/null || true)"
  if [ "$INSTALLED_SPEC" != "$PIPER_SPEC" ] || ! "$VENV_DIR/bin/python" -c 'import piper' >/dev/null 2>&1; then
    "$VENV_DIR/bin/python" -m pip install "$PIPER_SPEC"
    printf '%s\n' "$PIPER_SPEC" > "$MARKER"
  fi
fi
