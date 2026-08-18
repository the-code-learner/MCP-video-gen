from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_semver():
    version = (ROOT / "VERSION").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_changelog_contains_version():
    version = (ROOT / "VERSION").read_text().strip()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{version}]" in changelog


def test_portainer_yaml_exists():
    assert (ROOT / "video-mcp.yml").is_file()


def test_start_script_uses_persistent_venv_safely():
    script = (ROOT / "scripts" / "start.sh").read_text()
    assert 'find "$VENV_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +' in script
    assert 'rm -rf "$VENV_DIR"' not in script
