from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SECRET_KEY_RE = re.compile(
    r"(?:token|secret|password|passwd|authorization|api[_-]?key|credential|cookie|audience)",
    re.IGNORECASE,
)
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=_-]{256,}$")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact values before they are persisted to the audit database."""
    if depth > 5:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("http://", "https://")):
            stripped = _safe_url(stripped)
        if len(stripped) >= 256 and _BASE64ISH_RE.fullmatch(stripped):
            return f"<redacted-binary-like-string length={len(stripped)}>"
        if len(stripped) > 1200:
            return stripped[:1200] + f"… <truncated {len(stripped) - 1200} chars>"
        return stripped
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                out["<truncated>"] = f"{len(value) - 80} more keys"
                break
            name = str(key)
            out[name] = "<redacted>" if _SECRET_KEY_RE.search(name) else sanitize(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        limited = [sanitize(item, depth=depth + 1) for item in seq[:80]]
        if len(seq) > 80:
            limited.append(f"<truncated {len(seq) - 80} items>")
        return limited
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return sanitize(model_dump(), depth=depth + 1)
        except Exception:
            pass
    return sanitize(repr(value), depth=depth + 1)


def _result_summary(result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"type": type(result).__name__}
    is_error = getattr(result, "is_error", None)
    if is_error is not None:
        summary["is_error"] = bool(is_error)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        summary["structured_content"] = sanitize(structured)
    content = getattr(result, "content", None)
    if isinstance(content, list):
        summary["content_blocks"] = [type(item).__name__ for item in content[:20]]
        summary["content_count"] = len(content)
    return summary


class AuditLog:
    """Lazy, bounded SQLite audit store for MCP requests and WebGUI actions."""

    def __init__(self, data_root: Path) -> None:
        self.path = (data_root / "audit" / "events.sqlite3").resolve()
        self.enabled = _env_bool("AUDIT_LOG_ENABLED", True)
        self.retention_days = max(0, int(os.getenv("AUDIT_RETENTION_DAYS", "30")))
        self.max_rows = max(100, int(os.getenv("AUDIT_MAX_ROWS", "20000")))
        self._lock = threading.RLock()
        self._writes = 0

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_epoch REAL NOT NULL,
                source TEXT NOT NULL,
                method TEXT NOT NULL,
                tool TEXT,
                status TEXT NOT NULL,
                duration_ms REAL,
                arguments_json TEXT,
                result_json TEXT,
                error TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp_epoch DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_tool ON events(tool)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status)")
        return conn

    def _purge(self, conn: sqlite3.Connection) -> None:
        if self.retention_days > 0:
            cutoff = time.time() - self.retention_days * 86400
            conn.execute("DELETE FROM events WHERE timestamp_epoch < ?", (cutoff,))
        conn.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (self.max_rows,),
        )

    def record(
        self,
        *,
        source: str,
        method: str,
        status: str,
        tool: str = "",
        arguments: Any = None,
        result: Any = None,
        error: str = "",
        duration_ms: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        args_json = json.dumps(sanitize(arguments), ensure_ascii=False) if arguments is not None else None
        result_json = json.dumps(sanitize(result), ensure_ascii=False) if result is not None else None
        error_text = str(sanitize(error))[:4000] if error else None
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO events
                    (timestamp_epoch, source, method, tool, status, duration_ms, arguments_json, result_json, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        source[:40],
                        method[:200],
                        tool[:200] or None,
                        status[:40],
                        duration_ms,
                        args_json,
                        result_json,
                        error_text,
                    ),
                )
                self._writes += 1
                if self._writes % 100 == 0:
                    self._purge(conn)

    def list_events(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        source: str = "",
        status: str = "",
        tool: str = "",
        method: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "events": [], "count": 0}
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("source", source), ("status", status), ("tool", tool), ("method", method)):
            if value:
                clauses.append(f"{column} LIKE ?")
                params.append(f"%{value}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
        with self._lock:
            with self._connect() as conn:
                count = int(conn.execute(f"SELECT COUNT(*) FROM events{where}", params).fetchone()[0])
                rows = conn.execute(
                    f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
        events = []
        for row in rows:
            events.append(
                {
                    "id": row["id"],
                    "timestamp_epoch": row["timestamp_epoch"],
                    "source": row["source"],
                    "method": row["method"],
                    "tool": row["tool"],
                    "status": row["status"],
                    "duration_ms": row["duration_ms"],
                    "arguments": json.loads(row["arguments_json"]) if row["arguments_json"] else None,
                    "result": json.loads(row["result_json"]) if row["result_json"] else None,
                    "error": row["error"],
                }
            )
        return {
            "enabled": True,
            "count": count,
            "limit": limit,
            "offset": offset,
            "events": events,
            "retention_days": self.retention_days,
            "max_rows": self.max_rows,
        }


def install_mcp_audit_middleware(mcp: Any, audit: AuditLog) -> None:
    """Observe every inbound MCP message using the SDK's server middleware seam."""

    async def audit_middleware(ctx: Any, call_next: Any) -> Any:
        started = time.perf_counter()
        method = str(getattr(ctx, "method", "unknown"))
        params = getattr(ctx, "params", None)
        tool = ""
        arguments: Any = params
        if method == "tools/call" and isinstance(params, dict):
            tool = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
        try:
            result = await call_next(ctx)
        except Exception as exc:
            audit.record(
                source="mcp",
                method=method,
                tool=tool,
                status="error",
                arguments=arguments,
                error=str(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        audit.record(
            source="mcp",
            method=method,
            tool=tool,
            status="success",
            arguments=arguments,
            result=_result_summary(result),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return result

    mcp.middleware.append(audit_middleware)
