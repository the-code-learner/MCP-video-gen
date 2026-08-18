#!/bin/sh
set -eu

APP_DIR="${VIDEO_MCP_APP_DIR:-/opt/video-mcp/current}"
VENV_DIR="${VIDEO_MCP_VENV_DIR:-/opt/venv}"
DATA_ROOT="${VIDEO_MCP_DATA_ROOT:-/data}"
REQ_FILE="$APP_DIR/requirements.txt"
REQ_HASH_FILE="$VENV_DIR/.requirements.sha256"
SYSTEM_MARKER="/opt/video-mcp-system-deps-v3"

if [ ! -f "$SYSTEM_MARKER" ]; then
  apt-get update
  apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ffmpeg ca-certificates curl unzip \
    cmake build-essential pkg-config autoconf automake libtool \
    aubio-tools \
    libgbm1 libnss3 libatk-bridge2.0-0 libdrm2 \
    libxcomposite1 libxdamage1 libxrandr2 libcups2 libasound2 \
    libpangocairo-1.0-0 libxshmfence1 libgtk-3-0 \
    fonts-liberation fonts-noto-color-emoji fonts-noto-cjk fonts-noto-core \
    fonts-noto-extra fonts-noto-ui-core fonts-freefont-ttf fonts-dejavu-core \
    fontconfig
  rm -rf /var/lib/apt/lists/*
  apt-get clean
  fc-cache -f >/dev/null 2>&1 || true
  touch "$SYSTEM_MARKER"
fi

if [ ! -f "$REQ_FILE" ]; then
  echo "requirements.txt not found at $REQ_FILE" >&2
  exit 1
fi

CURRENT_HASH="$(sha256sum "$REQ_FILE" | awk '{print $1}')"
OLD_HASH=""
if [ -f "$REQ_HASH_FILE" ]; then
  OLD_HASH="$(cat "$REQ_HASH_FILE")"
fi

if [ ! -x "$VENV_DIR/bin/python" ] || [ "$CURRENT_HASH" != "$OLD_HASH" ]; then
  mkdir -p "$VENV_DIR"
  find "$VENV_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$REQ_FILE"
  printf '%s\n' "$CURRENT_HASH" > "$REQ_HASH_FILE"
fi

mkdir -p \
  "$DATA_ROOT/exports" \
  "$DATA_ROOT/tmp" \
  "$DATA_ROOT/hyperframes/projects" \
  "$DATA_ROOT/hyperframes-home" \
  "$DATA_ROOT/timelines" \
  "$DATA_ROOT/models" \
  "$DATA_ROOT/tooling" \
  "$DATA_ROOT/piper/voices"

HF_SPEC="${HYPERFRAMES_NPM_SPEC:-hyperframes@0.7.111}"
HF_MARKER="$DATA_ROOT/.hyperframes-installed-spec"
if ! command -v hyperframes >/dev/null 2>&1 || [ "$(cat "$HF_MARKER" 2>/dev/null || true)" != "$HF_SPEC" ]; then
  npm install --global --omit=dev --omit=optional "$HF_SPEC"
  printf '%s\n' "$HF_SPEC" > "$HF_MARKER"
fi

HOME="${HOME:-$DATA_ROOT/hyperframes-home}" \
HYPERFRAMES_SKIP_SKILLS=1 \
hyperframes browser ensure

/bin/sh "$APP_DIR/scripts/prepare_media_tools.sh"

APP_VERSION="$(cat "$APP_DIR/VERSION" 2>/dev/null || printf 'unknown')"
export VIDEO_MCP_APP_VERSION="$APP_VERSION"
export PYTHONPATH="$APP_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

RUN_UID="${MCP_RUN_UID:-65534}"
RUN_GID="${MCP_RUN_GID:-65534}"
chown -R "$RUN_UID:$RUN_GID" "$DATA_ROOT" "$VENV_DIR"

exec "$VENV_DIR/bin/python" -m video_mcp.entrypoint
