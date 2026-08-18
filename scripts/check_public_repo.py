from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".sh", ".txt", ".json", ".toml"}

# Public-repository guardrails. These patterns deliberately target secret material
# and common private-network literals, not normal generic documentation.
FORBIDDEN = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)cf[-_]?access[-_]?client[-_]?secret\s*[:=]\s*[^\s\"']+"),
    re.compile(r"(?i)cloudflared[_-]?tunnel[_-]?token\s*[:=]\s*[A-Za-z0-9._-]{20,}"),
    re.compile(r"(?i)(?:password|api[_-]?key|secret|token)\s*[:=]\s*[\"'][^\"']{12,}[\"']"),
    re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
]

# Compose/shell interpolation placeholders such as ${TOKEN:-} are configuration
# references, not committed secret values. Remove only the interpolation itself
# before scanning so quoted placeholders cannot trigger the literal-secret rules.
ENV_INTERPOLATION = re.compile(r"\$\{[^}\n]{1,256}\}")

failures = []
for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    scan_text = ENV_INTERPOLATION.sub("", text)
    for pattern in FORBIDDEN:
        match = pattern.search(scan_text)
        if match:
            failures.append(f"{path.relative_to(ROOT)}: matched {pattern.pattern!r}")

if failures:
    print("Public repository safety check failed:", file=sys.stderr)
    print("\n".join(f"- {item}" for item in failures), file=sys.stderr)
    raise SystemExit(1)

print("Public repository safety check passed.")
