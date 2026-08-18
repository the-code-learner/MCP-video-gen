#!/usr/bin/env python3
"""Minimal authenticated host bridge for Blender background jobs.

This service is intentionally separate from the MCP container. Run it on the
VM as a dedicated, unprivileged OS user. The MCP uploads cached inputs, submits
one Python job, and downloads only explicitly declared outputs.

Blender Python is powerful and is NOT sandboxed by this service. OS-level
isolation of the bridge user is the security boundary.
"""

from __future__ import annotations

import hmac
import json
import os
import shutil
import subprocess
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

BIND = os.getenv("BLENDER_BRIDGE_BIND", "127.0.0.1")
PORT = int(os.getenv("BLENDER_BRIDGE_PORT", "9876"))
TOKEN = os.getenv("BLENDER_BRIDGE_TOKEN", "")
ROOT = Path(os.getenv("BLENDER_BRIDGE_ROOT", "/var/lib/mcp-blender-bridge")).resolve()
BLENDER = os.getenv("BLENDER_BINARY", "blender")
MAX_INPUT_MB = int(os.getenv("BLENDER_BRIDGE_MAX_INPUT_MB", "512"))
MAX_SCRIPT_CHARS = int(os.getenv("BLENDER_BRIDGE_MAX_SCRIPT_CHARS", "500000"))
MAX_TIMEOUT = int(os.getenv("BLENDER_BRIDGE_MAX_TIMEOUT_SEC", "7200"))
LOG_CHARS = int(os.getenv("BLENDER_BRIDGE_MAX_LOG_CHARS", "30000"))


def safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("path must be relative and may not contain '..'")
    return Path(*pure.parts)


def job_dir(job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id.lower()):
        raise ValueError("invalid job id")
    path = (ROOT / "jobs" / job_id).resolve()
    if not path.is_relative_to((ROOT / "jobs").resolve()):
        raise ValueError("invalid job path")
    return path


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "MCPBlenderBridge/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("blender-bridge: " + (fmt % args) + "\n")

    def _authorized(self) -> bool:
        if not TOKEN:
            return False
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {TOKEN}"
        return hmac.compare_digest(header, expected)

    def _send_json(self, status: int, payload: object) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return False

    def _read_json(self, max_bytes: int = 1024 * 1024) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > max_bytes:
            raise ValueError("invalid JSON body length")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _write_request_body(self, destination: Path) -> int:
        limit = MAX_INPUT_MB * 1024 * 1024
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        transfer = self.headers.get("Transfer-Encoding", "").lower()
        with destination.open("wb") as out:
            if "chunked" in transfer:
                while True:
                    line = self.rfile.readline(128)
                    if not line:
                        raise ValueError("unexpected EOF in chunked request")
                    size_text = line.split(b";", 1)[0].strip()
                    size = int(size_text, 16)
                    if size == 0:
                        while True:
                            trailer = self.rfile.readline(8192)
                            if trailer in (b"\r\n", b"\n", b""):
                                break
                        break
                    total += size
                    if total > limit:
                        raise ValueError("input exceeds BLENDER_BRIDGE_MAX_INPUT_MB")
                    remaining = size
                    while remaining:
                        data = self.rfile.read(min(1024 * 1024, remaining))
                        if not data:
                            raise ValueError("unexpected EOF in request body")
                        out.write(data)
                        remaining -= len(data)
                    if self.rfile.read(2) != b"\r\n":
                        raise ValueError("invalid chunk framing")
            else:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length < 0 or length > limit:
                    raise ValueError("input exceeds BLENDER_BRIDGE_MAX_INPUT_MB")
                remaining = length
                while remaining:
                    data = self.rfile.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError("unexpected EOF in request body")
                    out.write(data)
                    total += len(data)
                    remaining -= len(data)
        return total

    def do_GET(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/v1/health":
            binary = shutil.which(BLENDER)
            if not binary:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "available": False, "error": f"Blender binary not found: {BLENDER}"},
                )
                return
            try:
                proc = subprocess.run(
                    [binary, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=15,
                    check=False,
                )
                first = (proc.stdout or "").splitlines()[0] if proc.stdout else "unknown"
            except Exception as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "available": False, "error": str(exc)})
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "available": True, "blender_binary": binary, "version": first},
            )
            return

        parts = [p for p in path.split("/") if p]
        if len(parts) >= 6 and parts[:2] == ["v1", "jobs"] and parts[3] == "outputs":
            # /v1/jobs/<id>/outputs/<relative/path>
            job_id = parts[2]
            relative = safe_relative("/".join(parts[4:]))
            root = job_dir(job_id)
            state_path = root / "state.json"
            if not state_path.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "job not found"})
                return
            state = json.loads(state_path.read_text(encoding="utf-8"))
            expected = {str(safe_relative(x)) for x in state.get("expected_outputs", [])}
            if str(relative) not in expected:
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "output was not declared"})
                return
            output = (root / "outputs" / relative).resolve()
            if not output.is_relative_to((root / "outputs").resolve()) or not output.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "output not found"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(output.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with output.open("rb") as handle:
                shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/v1/jobs":
                payload = self._read_json(max_bytes=2 * 1024 * 1024)
                script = str(payload.get("script", ""))
                if not script or len(script) > MAX_SCRIPT_CHARS:
                    raise ValueError("invalid script length")
                outputs_raw = payload.get("expected_outputs") or []
                if not isinstance(outputs_raw, list) or len(outputs_raw) > 100:
                    raise ValueError("expected_outputs must be a list of at most 100 paths")
                expected_outputs = [str(safe_relative(str(item))) for item in outputs_raw]
                timeout = int(payload.get("timeout_seconds") or 300)
                timeout = max(1, min(MAX_TIMEOUT, timeout))
                jid = uuid.uuid4().hex
                root = job_dir(jid)
                (root / "inputs").mkdir(parents=True, exist_ok=False)
                (root / "outputs").mkdir(parents=True, exist_ok=True)
                (root / "job.py").write_text(script, encoding="utf-8")
                state = {
                    "job_id": jid,
                    "expected_outputs": expected_outputs,
                    "timeout_seconds": timeout,
                    "ran": False,
                }
                (root / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
                self._send_json(HTTPStatus.CREATED, {"ok": True, "job_id": jid})
                return

            parts = [p for p in path.split("/") if p]
            if len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[3] == "run":
                jid = parts[2]
                root = job_dir(jid)
                state_path = root / "state.json"
                if not state_path.is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "job not found"})
                    return
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("ran"):
                    self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "job already ran"})
                    return
                binary = shutil.which(BLENDER)
                if not binary:
                    self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Blender binary not found"})
                    return
                env = os.environ.copy()
                env.update(
                    {
                        "BLENDER_JOB_DIR": str(root),
                        "BLENDER_INPUT_DIR": str((root / "inputs").resolve()),
                        "BLENDER_OUTPUT_DIR": str((root / "outputs").resolve()),
                    }
                )
                command = [
                    binary,
                    "--background",
                    "--factory-startup",
                    "--disable-autoexec",
                    "--python-exit-code",
                    "23",
                    "--python",
                    str(root / "job.py"),
                ]
                try:
                    proc = subprocess.run(
                        command,
                        cwd=root,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=int(state["timeout_seconds"]),
                        check=False,
                    )
                    returncode = proc.returncode
                    stdout = (proc.stdout or "")[-LOG_CHARS:]
                    stderr = (proc.stderr or "")[-LOG_CHARS:]
                except subprocess.TimeoutExpired as exc:
                    returncode = 124
                    stdout = (exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))[-LOG_CHARS:]
                    stderr = (exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))[-LOG_CHARS:]
                    stderr += "\nBlender job timed out."
                state["ran"] = True
                state["returncode"] = returncode
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                outputs = []
                for relative_text in state.get("expected_outputs", []):
                    relative = safe_relative(relative_text)
                    output = (root / "outputs" / relative).resolve()
                    if output.is_relative_to((root / "outputs").resolve()) and output.is_file():
                        outputs.append({"path": relative_text, "size_bytes": output.stat().st_size})
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": returncode == 0,
                        "job_id": jid,
                        "returncode": returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "outputs": outputs,
                    },
                )
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_PUT(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = unquote(urlsplit(self.path).path)
        parts = [p for p in path.split("/") if p]
        try:
            if len(parts) >= 5 and parts[:2] == ["v1", "jobs"] and parts[3] == "inputs":
                jid = parts[2]
                relative = safe_relative("/".join(parts[4:]))
                root = job_dir(jid)
                if not (root / "state.json").is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "job not found"})
                    return
                destination = (root / "inputs" / relative).resolve()
                if not destination.is_relative_to((root / "inputs").resolve()):
                    raise ValueError("invalid input path")
                received = self._write_request_body(destination)
                self._send_json(HTTPStatus.OK, {"ok": True, "received_bytes": received, "path": str(relative)})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = unquote(urlsplit(self.path).path)
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["v1", "jobs"]:
            try:
                root = job_dir(parts[2])
                shutil.rmtree(root, ignore_errors=True)
                self._send_json(HTTPStatus.OK, {"ok": True, "deleted": parts[2]})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})


def main() -> None:
    if not TOKEN:
        raise SystemExit("BLENDER_BRIDGE_TOKEN must be set; refusing to start an unauthenticated Blender bridge")
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "jobs").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((BIND, PORT), BridgeHandler)
    print(f"Blender bridge listening on {BIND}:{PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
