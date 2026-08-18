from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

from .audit import AuditLog


def webgui_enabled() -> bool:
    raw = os.getenv("WEBGUI_ENABLED")
    return True if raw is None else raw.lower() in {"1", "true", "yes", "on"}


def _safe_filename(value: str) -> str:
    name = Path(value).name.replace("\x00", "").strip()
    if not name or name in {".", ".."}:
        raise ValueError("Invalid filename")
    return name[:255]


def _mutation_allowed(request: Request) -> bool:
    # A custom header makes cross-site form submissions insufficient for mutation.
    # No CORS policy is added, so another origin cannot set it from browser JS.
    return request.headers.get("x-mcp-webgui", "") == "1"


def _json_error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _load_meta(exports: Path, file_id: str) -> dict[str, Any]:
    path = exports / f"{file_id}.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _cache_rows(exports: Path, retention_manager: Any) -> list[dict[str, Any]]:
    rows = []
    for entry in retention_manager.entries():
        file_id = str(entry["file_id"])
        meta = _load_meta(exports, file_id)
        filename = str(entry["filename"])
        content_type = str(meta.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        rows.append(
            {
                "file_id": file_id,
                "filename": filename,
                "size_bytes": int(entry["size_bytes"]),
                "created_epoch": int(entry["created_epoch"]),
                "content_type": content_type,
                "source": str(meta.get("source") or "unknown"),
                "pinned": bool(entry["pinned"]),
                "pin_note": str(entry.get("pin_note") or ""),
                "download_path": f"/files/{file_id}",
            }
        )
    rows.sort(key=lambda row: (row["created_epoch"], row["file_id"]), reverse=True)
    return rows


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCP Video Gen</title>
<style>
:root{color-scheme:dark;--bg:#111318;--card:#1a1e26;--line:#303642;--text:#edf0f5;--muted:#9da7b7;--accent:#7aa2ff;--danger:#ff7070}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;gap:16px;align-items:center;padding:18px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(17,19,24,.96);z-index:5}
h1{font-size:18px;margin:0}.muted{color:var(--muted)}nav{margin-left:auto;display:flex;gap:8px}.tab,button,.button{border:1px solid var(--line);background:var(--card);color:var(--text);padding:8px 11px;border-radius:8px;cursor:pointer;text-decoration:none}.tab.active{border-color:var(--accent);color:#fff}button.danger{border-color:#6b3030;color:#ffaaaa}
main{padding:20px 24px;max-width:1500px;margin:auto}.panel{display:none}.panel.active{display:block}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}.grow{flex:1}.stat{padding:8px 12px;background:var(--card);border:1px solid var(--line);border-radius:8px}
input,select{background:#12151b;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:8px}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:middle}th{color:var(--muted);font-weight:600}.preview{width:72px;height:54px;object-fit:cover;border-radius:6px;background:#090b0f}.preview.audio{height:32px;width:180px}code{font-size:12px;color:#bcd0ff}.actions{display:flex;gap:6px;flex-wrap:wrap}.badge{display:inline-block;padding:2px 7px;border-radius:99px;border:1px solid var(--line);font-size:12px}.ok{color:#8ee6a3}.err{color:#ff9b9b}.pin{color:#ffd784}.empty{padding:40px;text-align:center;color:var(--muted)}details pre{white-space:pre-wrap;max-width:700px;max-height:280px;overflow:auto;color:#cfd7e6}.toast{position:fixed;right:20px;bottom:20px;background:#202631;border:1px solid var(--line);padding:11px 14px;border-radius:8px;display:none;z-index:10}
@media(max-width:900px){.hide-small{display:none}main{padding:14px}header{padding:14px}.preview{width:50px;height:40px}}
</style>
</head>
<body>
<header><h1>MCP Video Gen</h1><span id="version" class="muted"></span><nav><button class="tab active" data-tab="cache">Cache</button><button class="tab" data-tab="activity">Activity</button></nav></header>
<main>
<section id="cache" class="panel active">
<div class="toolbar"><label class="button">Upload file<input id="upload" type="file" hidden></label><input id="cacheSearch" class="grow" placeholder="Search filename, file_id, source…"><span id="cacheStats" class="stat">Loading…</span><button onclick="loadCache()">Refresh</button></div>
<div id="cacheTable"></div>
</section>
<section id="activity" class="panel">
<div class="toolbar"><input id="activityTool" placeholder="Tool / method filter"><select id="activitySource"><option value="">All sources</option><option value="mcp">MCP</option><option value="webgui">WebGUI</option></select><select id="activityStatus"><option value="">All statuses</option><option value="success">Success</option><option value="error">Error</option></select><button onclick="loadActivity()">Refresh</button><button onclick="downloadActivity()">Download TXT</button><span id="auditStats" class="stat"></span></div>
<div id="activityTable"></div>
</section>
</main><div id="toast" class="toast"></div>
<script>
const state={cache:[]};
const fmtBytes=n=>{if(n<1024)return n+' B';const u=['KB','MB','GB','TB'];let i=-1;do{n/=1024;i++}while(n>=1024&&i<u.length-1);return n.toFixed(n>=100?0:n>=10?1:2)+' '+u[i]};
const fmtTime=e=>new Date(e*1000).toLocaleString();
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(()=>t.style.display='none',2600)}
async function api(url,opts={}){const r=await fetch(url,opts);const j=await r.json().catch(()=>({error:r.statusText}));if(!r.ok)throw new Error(j.error||r.statusText);return j}
function preview(row){const p=row.download_path,m=row.content_type||'';if(m.startsWith('image/'))return `<img class="preview" loading="lazy" src="${p}">`;if(m.startsWith('video/'))return `<video class="preview" preload="metadata" src="${p}" controls></video>`;if(m.startsWith('audio/'))return `<audio class="preview audio" preload="metadata" src="${p}" controls></audio>`;return `<span class="badge">${m.split('/')[1]||'file'}</span>`}
function renderCache(){const q=document.getElementById('cacheSearch').value.toLowerCase();const rows=state.cache.filter(r=>!q||[r.filename,r.file_id,r.source,r.content_type].join(' ').toLowerCase().includes(q));if(!rows.length){document.getElementById('cacheTable').innerHTML='<div class="empty">No cached files</div>';return}document.getElementById('cacheTable').innerHTML=`<table><thead><tr><th>Preview</th><th>File</th><th class="hide-small">Type</th><th>Size</th><th class="hide-small">Created</th><th>Retention</th><th>Actions</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${preview(r)}</td><td><b>${esc(r.filename)}</b><br><code title="${r.file_id}">${r.file_id}</code><br><span class="muted">${esc(r.source)}</span></td><td class="hide-small">${esc(r.content_type)}</td><td>${fmtBytes(r.size_bytes)}</td><td class="hide-small">${fmtTime(r.created_epoch)}</td><td>${r.pinned?'<span class="badge pin">Pinned</span>':'<span class="muted">normal</span>'}</td><td><div class="actions"><a class="button" href="${r.download_path}" download>Download</a><button onclick="copyId('${r.file_id}')">Copy ID</button><button onclick="togglePin('${r.file_id}',${!r.pinned})">${r.pinned?'Unpin':'Pin'}</button><button class="danger" onclick="removeFile('${r.file_id}',${r.pinned})">Delete</button></div></td></tr>`).join('')}</tbody></table>`}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function loadCache(){const j=await api('/api/cache');state.cache=j.files;document.getElementById('cacheStats').textContent=`${j.status.file_count} files · ${fmtBytes(j.status.total_bytes)} · retention ${j.status.policy.automatic_cleanup}`;renderCache()}
async function togglePin(id,pin){await api(`/api/cache/${id}/${pin?'pin':'unpin'}`,{method:'POST',headers:{'X-MCP-WebGUI':'1'}});toast(pin?'Pinned':'Unpinned');await loadCache()}
async function removeFile(id,pinned){if(!confirm(pinned?'This file is pinned. Delete it anyway?':'Delete this cached file?'))return;await api(`/api/cache/${id}?force=${pinned?'true':'false'}`,{method:'DELETE',headers:{'X-MCP-WebGUI':'1'}});toast('Deleted');await loadCache()}
async function copyId(id){await navigator.clipboard.writeText(id);toast('file_id copied')}
function activityParams(){const term=document.getElementById('activityTool').value;const source=document.getElementById('activitySource').value;const status=document.getElementById('activityStatus').value;const qs=new URLSearchParams({source,status});if(term)qs.set('query',term);return qs}
function downloadActivity(){const qs=activityParams();window.location.assign('/api/audit.txt?'+qs)}
async function loadActivity(){const qs=activityParams();qs.set('limit','300');const j=await api('/api/audit?'+qs);document.getElementById('auditStats').textContent=j.enabled?`${j.count} events · ${j.retention_days}d / ${j.max_rows} max`:'Audit disabled';const rows=j.events||[];document.getElementById('activityTable').innerHTML=rows.length?`<table><thead><tr><th>Time</th><th>Source</th><th>Action</th><th>Status</th><th>Duration</th><th>Details</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${fmtTime(r.timestamp_epoch)}</td><td>${esc(r.source)}</td><td><b>${esc(r.tool||r.method)}</b><br><span class="muted">${esc(r.method)}</span></td><td class="${r.status==='success'?'ok':'err'}">${esc(r.status)}</td><td>${r.duration_ms==null?'':r.duration_ms.toFixed(1)+' ms'}</td><td><details><summary>View</summary><pre>${esc(JSON.stringify({arguments:r.arguments,result:r.result,error:r.error},null,2))}</pre></details></td></tr>`).join('')}</tbody></table>`:'<div class="empty">No activity</div>'}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab,.panel').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.tab).classList.add('active');if(b.dataset.tab==='activity')loadActivity()});
document.getElementById('cacheSearch').oninput=renderCache;
document.getElementById('activityTool').onkeydown=e=>{if(e.key==='Enter')loadActivity()};
document.getElementById('upload').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{toast('Uploading…');await api('/api/cache/upload?filename='+encodeURIComponent(f.name),{method:'PUT',headers:{'X-MCP-WebGUI':'1','Content-Type':f.type||'application/octet-stream'},body:f});toast('Upload complete');await loadCache()}catch(err){toast(err.message)}finally{e.target.value=''}};
api('/api/status').then(j=>document.getElementById('version').textContent=`v${j.app_version} · ${j.source_ref||''}`);loadCache().catch(e=>toast(e.message));
</script></body></html>"""


def create_webgui_routes(
    *,
    exports: Path,
    tmp: Path,
    cached: Callable[[str], Path],
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    retention_manager: Any,
    audit: AuditLog,
    max_upload_mb: int,
    app_version: str,
    source_ref: str,
) -> list[Route]:
    async def index(_: Request) -> HTMLResponse:
        return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})

    async def api_status(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "server": "video-mcp",
                "app_version": app_version,
                "source_ref": source_ref,
                "webgui": True,
                "audit_enabled": audit.enabled,
            }
        )

    async def api_cache(_: Request) -> JSONResponse:
        return JSONResponse({"files": _cache_rows(exports, retention_manager), "status": retention_manager.status()})

    async def api_upload(request: Request) -> JSONResponse:
        if not _mutation_allowed(request):
            return _json_error("Missing WebGUI mutation header", 403)
        try:
            filename = _safe_filename(request.query_params.get("filename", ""))
        except ValueError as exc:
            return _json_error(str(exc))
        limit = max_upload_mb * 1024 * 1024
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > limit:
            return _json_error("Upload exceeds MAX_UPLOAD_MB", 413)
        tmp.mkdir(parents=True, exist_ok=True)
        part = tmp / f"webgui-upload-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        total = 0
        started = time.perf_counter()
        try:
            with part.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("Upload exceeds MAX_UPLOAD_MB while streaming")
                    handle.write(chunk)
                    digest.update(chunk)
            if total == 0:
                raise ValueError("Upload is empty")
            file_id, out = target(filename)
            part.replace(out)
            meta = file_meta(
                file_id,
                out,
                "webgui-upload",
                sha256=digest.hexdigest(),
                request_content_type=request.headers.get("content-type", ""),
            )
            audit.record(
                source="webgui",
                method="cache.upload",
                status="success",
                arguments={"filename": filename, "size_bytes": total},
                result={"file_id": file_id},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            return JSONResponse({"ok": True, "file": meta})
        except Exception as exc:
            part.unlink(missing_ok=True)
            audit.record(
                source="webgui",
                method="cache.upload",
                status="error",
                arguments={"filename": filename, "size_bytes": total},
                error=str(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            status = 413 if "MAX_UPLOAD_MB" in str(exc) else 400
            return _json_error(str(exc), status)

    async def api_delete(request: Request) -> JSONResponse:
        if not _mutation_allowed(request):
            return _json_error("Missing WebGUI mutation header", 403)
        file_id = request.path_params["file_id"]
        force = request.query_params.get("force", "").lower() in {"1", "true", "yes", "on"}
        try:
            row = next((item for item in retention_manager.entries() if item["file_id"] == file_id), None)
            if row is None:
                raise ValueError("Cached file not found")
            if row["pinned"] and not force:
                return _json_error("File is pinned; unpin it or confirm forced deletion", 409)
            path = cached(file_id)
            size = path.stat().st_size
            filename = path.name.split("__", 1)[-1]
            path.unlink()
            (exports / f"{file_id}.json").unlink(missing_ok=True)
            audit.record(
                source="webgui",
                method="cache.delete",
                status="success",
                arguments={"file_id": file_id, "filename": filename, "forced": force},
                result={"deleted_bytes": size},
            )
            return JSONResponse({"ok": True, "file_id": file_id, "deleted_bytes": size})
        except ValueError as exc:
            return _json_error(str(exc), 404)
        except OSError as exc:
            audit.record(source="webgui", method="cache.delete", status="error", arguments={"file_id": file_id}, error=str(exc))
            return _json_error(str(exc), 500)

    async def api_pin(request: Request) -> JSONResponse:
        if not _mutation_allowed(request):
            return _json_error("Missing WebGUI mutation header", 403)
        file_id = request.path_params["file_id"]
        try:
            result = retention_manager.set_pinned(file_id, pinned=True, note="Pinned from WebGUI")
        except ValueError as exc:
            return _json_error(str(exc), 404)
        audit.record(source="webgui", method="cache.pin", status="success", arguments={"file_id": file_id})
        return JSONResponse({"ok": True, **result})

    async def api_unpin(request: Request) -> JSONResponse:
        if not _mutation_allowed(request):
            return _json_error("Missing WebGUI mutation header", 403)
        file_id = request.path_params["file_id"]
        try:
            result = retention_manager.set_pinned(file_id, pinned=False)
        except ValueError as exc:
            return _json_error(str(exc), 404)
        audit.record(source="webgui", method="cache.unpin", status="success", arguments={"file_id": file_id})
        return JSONResponse({"ok": True, **result})

    def audit_filters(request: Request) -> dict[str, str]:
        q = request.query_params
        return {
            "source": q.get("source", ""),
            "status": q.get("status", ""),
            "tool": q.get("tool", ""),
            "method": q.get("method", ""),
            "query": q.get("query", ""),
        }

    async def api_audit(request: Request) -> JSONResponse:
        q = request.query_params
        try:
            data = audit.list_events(
                limit=int(q.get("limit", "200")),
                offset=int(q.get("offset", "0")),
                **audit_filters(request),
            )
        except ValueError:
            return _json_error("Invalid pagination")
        return JSONResponse(data)

    async def api_audit_txt(request: Request) -> StreamingResponse:
        filters = audit_filters(request)
        summary = audit.list_events(limit=1, **filters)
        generated = datetime.now(timezone.utc)
        filename = f"mcp-video-gen-activity-{generated.strftime('%Y%m%d-%H%M%SZ')}.txt"

        def stream():
            active_filters = {key: value for key, value in filters.items() if value}
            yield "MCP Video Gen activity export\n"
            yield f"Version: {app_version} · {source_ref}\n"
            yield f"Generated UTC: {generated.isoformat().replace('+00:00', 'Z')}\n"
            yield f"Events: {summary.get('count', 0)}\n"
            yield f"Filters: {json.dumps(active_filters, ensure_ascii=False) if active_filters else 'none'}\n"
            yield "Security: values are exported from the sanitized audit store; secrets, signed-URL query strings and long binary/base64 payloads are not intentionally persisted.\n"
            yield "Order: newest first\n\n"
            if not summary.get("enabled"):
                yield "Audit disabled.\n"
                return
            for event in audit.iter_events(**filters):
                timestamp = datetime.fromtimestamp(float(event["timestamp_epoch"]), timezone.utc)
                timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
                duration = event.get("duration_ms")
                duration_text = "" if duration is None else f"{float(duration):.3f} ms"
                yield "=" * 88 + "\n"
                yield f"Event ID: {event['id']}\n"
                yield f"Time UTC: {timestamp_text}\n"
                yield f"Timestamp epoch: {event['timestamp_epoch']}\n"
                yield f"Source: {event['source']}\n"
                yield f"Action: {event.get('tool') or event['method']}\n"
                yield f"Method: {event['method']}\n"
                yield f"Status: {event['status']}\n"
                yield f"Duration: {duration_text}\n"
                yield "Arguments:\n"
                yield json.dumps(event.get("arguments"), ensure_ascii=False, indent=2) + "\n"
                yield "Result:\n"
                yield json.dumps(event.get("result"), ensure_ascii=False, indent=2) + "\n"
                yield "Error:\n"
                yield (event.get("error") or "null") + "\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    return [
        Route("/", index, methods=["GET"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/cache", api_cache, methods=["GET"]),
        Route("/api/cache/upload", api_upload, methods=["PUT"]),
        Route("/api/cache/{file_id}", api_delete, methods=["DELETE"]),
        Route("/api/cache/{file_id}/pin", api_pin, methods=["POST"]),
        Route("/api/cache/{file_id}/unpin", api_unpin, methods=["POST"]),
        Route("/api/audit", api_audit, methods=["GET"]),
        Route("/api/audit.txt", api_audit_txt, methods=["GET"]),
    ]
