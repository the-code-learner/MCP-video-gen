from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_media_tools.sh"

CORRECT_SHA = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
LEGACY_BAD_SHA = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
OFFICIAL_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/"
    "src/silero_vad/data/silero_vad.onnx"
)


def test_silero_v621_uses_verified_checksum_and_legacy_compatibility():
    text = SCRIPT.read_text(encoding="utf-8")

    assert f'SILERO_DEFAULT_URL="{OFFICIAL_URL}"' in text
    assert f'SILERO_DEFAULT_SHA="{CORRECT_SHA}"' in text
    assert f'SILERO_LEGACY_BAD_SHA="{LEGACY_BAD_SHA}"' in text

    # Compatibility must be tightly scoped to the exact official URL and the
    # one known-bad v2.4.2 default pin, so custom URLs/pins remain strict.
    assert '[ "$SILERO_URL" = "$SILERO_DEFAULT_URL" ]' in text
    assert '[ "$SILERO_SHA" = "$SILERO_LEGACY_BAD_SHA" ]' in text
    assert 'SILERO_SHA="$SILERO_DEFAULT_SHA"' in text
    assert 'download_verified "$SILERO_URL" "$SILERO_SHA" "$SILERO_PATH"' in text
