from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .model_manager import catalog_with_status, load_selection
from .qwen_runtime import QWEN_PACKAGE_SPEC
from .runtime_resources import gpu_snapshot, ram_snapshot, storage_snapshot


SYSTEM_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCP Video Gen · System</title>
<style>
:root{color-scheme:dark;--bg:#111318;--card:#1a1e26;--line:#303642;--text:#edf0f5;--muted:#9da7b7;--accent:#7aa2ff;--ok:#8ee6a3;--warn:#ffd784}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,-apple-system,Segoe UI,sans-serif}header{display:flex;gap:14px;align-items:center;padding:18px 24px;border-bottom:1px solid var(--line)}h1{font-size:18px;margin:0}a,button{border:1px solid var(--line);background:var(--card);color:var(--text);padding:8px 11px;border-radius:8px;text-decoration:none;cursor:pointer}.muted{color:var(--muted)}main{max-width:1300px;margin:auto;padding:20px 24px}.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px}.card h2{font-size:15px;margin:0 0 12px}.metric{display:flex;justify-content:space-between;gap:12px;border-top:1px solid var(--line);padding:8px 0}.metric:first-of-type{border-top:0}.value{font-variant-numeric:tabular-nums}table{width:100%;border-collapse:collapse}.models{margin-top:14px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.badge{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:2px 7px}.ok{color:var(--ok)}.warn{color:var(--warn)}pre{white-space:pre-wrap;overflow:auto;color:#cfd7e6}.empty{padding:25px;color:var(--muted)}
</style>
</head>
<body>
<header><h1>MCP Video Gen · System</h1><span class="muted">read-only telemetry</span><span style="flex:1"></span><a href="/">Cache / Activity</a></header>
<main>
<div class="toolbar"><button onclick="loadSystem()">Refresh</button><span class="muted">RAM attribution uses the Video Gen cgroup; VRAM outside the registered Qwen worker is external/unattributed.</span></div>
<div id="cards" class="grid"><div class="card">Loading…</div></div>
<div id="models" class="models"></div>
</main>
<script>
const fmt=n=>{if(n==null)return 'n/a';if(n<1024)return n+' B';const u=['KB','MB','GB','TB'];let i=-1;do{n/=1024;i++}while(n>=1024&&i<u.length-1);return n.toFixed(n>=100?0:n>=10?1:2)+' '+u[i]};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const row=(k,v)=>`<div class="metric"><span class="muted">${esc(k)}</span><span class="value">${esc(v)}</span></div>`;
async function loadSystem(){const r=await fetch('/api/system',{cache:'no-store'});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);const s=j.storage||{},ram=j.ram||{},gpu=j.gpu||{},worker=j.qwen_worker||{};const host=ram.host_visible||{},cg=ram.video_gen_container||{},ext=ram.external_or_shared_estimate||{};let cards=[];cards.push(`<div class="card"><h2>Storage /data</h2>${row('Total',fmt(s.total_bytes))}${row('Used',fmt(s.used_bytes))}${row('Free',fmt(s.free_bytes))}${row('Used',s.used_percent==null?'n/a':s.used_percent+'%')}</div>`);cards.push(`<div class="card"><h2>RAM</h2>${row('VM visible total',fmt(host.total_bytes))}${row('VM available',fmt(host.available_bytes))}${row('Video Gen cgroup',fmt(cg.current_bytes))}${row('External/shared estimate',fmt(ext.bytes))}${row('Pressure',host.pressure||'unknown')}</div>`);if(gpu.available&&(gpu.devices||[]).length){for(const d of gpu.devices){const own=d.video_gen_worker||{},other=d.external_or_unattributed_estimate||{};cards.push(`<div class="card"><h2>GPU ${esc(d.index)} · ${esc(d.name)}</h2>${row('Total VRAM',fmt(d.total_bytes))}${row('Used VRAM',fmt(d.used_bytes))}${row('Free VRAM',fmt(d.free_bytes))}${row('Video Gen reserved',fmt(own.reserved_bytes))}${row('External/unattributed',fmt(other.bytes))}${row('GPU utilization',(d.utilization_percent??'n/a')+'%')}${row('Pressure',d.pressure||'unknown')}</div>`);}}else{cards.push(`<div class="card"><h2>GPU</h2><div class="empty">NVIDIA telemetry unavailable.</div></div>`)}cards.push(`<div class="card"><h2>Qwen worker</h2>${row('Runtime',j.qwen_runtime_installed?'installed':'not installed')}${row('Worker',worker.running?'running':'stopped')}${row('Model loaded',worker.model_loaded?'yes':'no')}${row('Worker RSS',fmt(worker.rss_bytes))}${row('Selected profile',(j.selection||{})['qwen3-tts']||'none')}</div>`);document.getElementById('cards').innerHTML=cards.join('');let rows=[];for(const [family,profiles] of Object.entries((j.models||{}).families||{})){for(const [profile,m] of Object.entries(profiles)){rows.push(`<tr><td>${esc(family)}</td><td>${esc(profile)}</td><td>${esc(m.label)}</td><td>${m.installed?'<span class="badge ok">installed</span>':'<span class="badge">optional</span>'}</td><td>${fmt(m.size_bytes)}</td></tr>`)}}document.getElementById('models').innerHTML=`<div class="card"><h2>Optional model catalog</h2><table><thead><tr><th>Family</th><th>Profile</th><th>Model</th><th>Status</th><th>Artifact</th></tr></thead><tbody>${rows.join('')}</tbody></table><p class="muted">Models above 100 MiB are not preloaded. Installation/removal and cache reclamation remain approval-gated MCP operations.</p></div>`;}
loadSystem().catch(e=>document.getElementById('cards').innerHTML=`<div class="card"><h2>Error</h2><pre>${esc(e.message)}</pre></div>`);
</script>
</body></html>"""


def create_system_webgui_routes(
    *,
    data_root: Path,
    worker_metrics: Callable[[], dict[str, Any]],
    runtime_installed: Callable[[], bool],
) -> list[Route]:
    async def system_page(_: Request) -> HTMLResponse:
        return HTMLResponse(SYSTEM_HTML, headers={"Cache-Control": "no-store"})

    async def api_system(_: Request) -> JSONResponse:
        metrics = worker_metrics()
        return JSONResponse(
            {
                "storage": storage_snapshot(data_root, include_breakdown=True),
                "ram": ram_snapshot(),
                "gpu": gpu_snapshot(metrics),
                "qwen_worker": metrics,
                "qwen_runtime_installed": runtime_installed(),
                "qwen_runtime_spec": QWEN_PACKAGE_SPEC,
                "selection": load_selection(data_root),
                "models": catalog_with_status(data_root),
                "safety": {
                    "read_only": True,
                    "host_pid_namespace": False,
                    "docker_socket": False,
                    "external_processes_mutable": False,
                },
            }
        )

    return [
        Route("/system", system_page, methods=["GET"]),
        Route("/api/system", api_system, methods=["GET"]),
    ]
