from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "video-mcp.yml"


def _command_block() -> str:
    text = STACK.read_text(encoding="utf-8")
    start = text.index("    command:\n")
    end = text.index("\n  cloudflared:\n", start)
    return text[start:end]


def test_inline_bootstrap_dollars_are_compose_escaped() -> None:
    block = _command_block()

    # Every dollar sign in the inline shell must be emitted as a Compose `$$`
    # escape. Docker Compose will then pass one literal `$` to /bin/sh.
    assert "$" not in block.replace("$$", "")

    # Guard the specific shell-local variables that previously became empty
    # during `docker compose config`, producing `mkdir -p ""` at runtime.
    for expected in (
        'RELEASES="$$ROOT/releases"',
        'CURRENT="$$ROOT/current"',
        'mkdir -p "$$RELEASES"',
        'REF="$$VERSION_POLICY"',
        'TARGET="$$RELEASES/$$SAFE_REF"',
        'STAGING="$$RELEASES/.staging-$$SAFE_REF-$$$$"',
        'NEW_LINK="$$ROOT/.current-$$$$"',
        'REF="$$(resolve_latest)"',
    ):
        assert expected in block
