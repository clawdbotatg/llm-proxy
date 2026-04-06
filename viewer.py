#!/usr/bin/env python3
"""
LLM Proxy Viewer — read-only web UI for browsing proxy logs.

Usage:
    python viewer.py
    VIEWER_PORT=8802 python viewer.py

Open http://localhost:8801
"""

import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("VIEWER_PORT", "8801"))
LOG_DIR = os.environ.get(
    "PROXY_LOG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_logs"),
)


def read_all_calls(after=0):
    path = os.path.join(LOG_DIR, "all_calls.jsonl")
    calls = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    c = json.loads(line)
                    if c.get("global_id", 0) > after:
                        calls.append(c)
    except FileNotFoundError:
        pass
    return calls


def read_jobs():
    jobs = []
    try:
        for name in sorted(os.listdir(LOG_DIR)):
            job_dir = os.path.join(LOG_DIR, name)
            if not os.path.isdir(job_dir):
                continue
            manifest_path = os.path.join(job_dir, "manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                    jobs.append({k: v for k, v in manifest.items() if k != "calls"})
                except (json.JSONDecodeError, OSError):
                    pass
    except FileNotFoundError:
        pass
    return jobs


def read_call_detail(job_name, call_id):
    job_dir = os.path.join(LOG_DIR, job_name)
    result = {}
    for kind in ("request", "response"):
        path = os.path.join(job_dir, f"{call_id:03d}_{kind}.json")
        try:
            with open(path) as f:
                result[kind] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            result[kind] = None
    return result


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LLM Proxy</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'SF Mono','Fira Code',monospace; font-size: 12px; background: #0d1117; color: #e6edf3; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

/* stats bar */
#stats { display: flex; align-items: center; gap: 24px; padding: 8px 16px; background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0; }
.stat { display: flex; flex-direction: column; }
.stat-label { color: #7d8590; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-value { font-size: 15px; font-weight: bold; }
#live { display: flex; align-items: center; gap: 6px; color: #7d8590; font-size: 11px; margin-right: 8px; }
#live-dot { width: 6px; height: 6px; border-radius: 50%; background: #3fb950; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

/* layout */
#main { display: flex; flex: 1; overflow: hidden; }

/* jobs panel */
#jobs-panel { width: 210px; border-right: 1px solid #30363d; display: flex; flex-direction: column; flex-shrink: 0; }
#jobs-panel h2 { padding: 7px 12px; color: #7d8590; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #30363d; flex-shrink: 0; }
#jobs-list { overflow-y: auto; flex: 1; }
.job-item { padding: 9px 12px; border-bottom: 1px solid #21262d; cursor: pointer; user-select: none; }
.job-item:hover { background: #161b22; }
.job-item.active { background: #1c2128; border-left: 2px solid #58a6ff; padding-left: 10px; }
.job-name { color: #58a6ff; font-weight: bold; margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-meta { color: #7d8590; font-size: 11px; margin-top: 1px; }
.job-cost { color: #3fb950; }

/* calls area */
#calls-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
.col-header { display: grid; grid-template-columns: 36px 90px 110px 180px minmax(0,2fr) 76px 56px 44px; gap: 8px; padding: 6px 12px; background: #161b22; border-bottom: 1px solid #30363d; color: #7d8590; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0; }
#calls-list { flex: 1; overflow-y: auto; }
.call-row { display: grid; grid-template-columns: 36px 90px 110px 180px minmax(0,2fr) 76px 56px 44px; gap: 8px; align-items: center; padding: 5px 12px; border-bottom: 1px solid #21262d; cursor: pointer; user-select: none; }
.call-row:hover { background: #161b22; }
.call-row.selected { background: #1c2128; }
.c-id { color: #7d8590; }
.c-job { color: #58a6ff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-model { color: #d2a8ff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-in { color: #79c0ff; }
.c-out { color: #ffa657; }
.c-cost { color: #3fb950; text-align: right; }
.c-elapsed { text-align: right; }
.c-status { text-align: center; }
.ok { color: #3fb950; }
.err { color: #f85149; }
.bar-wrap { display: flex; align-items: center; gap: 6px; }
.tbar { flex: 1; height: 3px; background: #21262d; border-radius: 2px; }
.tbar-fill { height: 100%; background: #79c0ff; border-radius: 2px; }

/* detail drawer */
#detail { border-top: 1px solid #30363d; display: flex; flex-direction: column; height: 42%; flex-shrink: 0; }
#detail.hidden { display: none; }
#detail-hdr { display: flex; justify-content: space-between; align-items: center; padding: 6px 12px; background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0; }
#detail-title { color: #7d8590; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#detail-close { background: none; border: none; color: #7d8590; cursor: pointer; font-size: 13px; padding: 0 4px; }
#detail-close:hover { color: #e6edf3; }
#detail-body { flex: 1; display: flex; overflow: hidden; }
.dpane { flex: 1; display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid #30363d; }
.dpane:last-child { border-right: none; }
.dpane-hdr { padding: 5px 12px; background: #161b22; border-bottom: 1px solid #30363d; color: #7d8590; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0; }
.dpane-body { flex: 1; overflow-y: auto; }

/* messages */
.msg { padding: 7px 12px; border-bottom: 1px solid #21262d; }
.msg-role { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px; }
.r-system { color: #ff7b72; }
.r-user { color: #79c0ff; }
.r-assistant { color: #d2a8ff; }
.r-tool { color: #ffa657; }
.r-meta { color: #7d8590; }
.msg-body { white-space: pre-wrap; word-break: break-word; max-height: 180px; overflow-y: auto; color: #e6edf3; line-height: 1.5; }
.msg-body.open { max-height: none; }
.more-btn { color: #58a6ff; font-size: 10px; cursor: pointer; margin-top: 3px; display: inline-block; }
.more-btn:hover { text-decoration: underline; }
.tool-call { background: #1c2128; border: 1px solid #30363d; border-radius: 4px; padding: 5px 8px; margin-top: 4px; }
.tc-name { color: #ffa657; font-weight: bold; margin-bottom: 3px; }
.tc-args { color: #8b949e; white-space: pre-wrap; max-height: 80px; overflow-y: auto; font-size: 11px; }
.tc-args.open { max-height: none; }
.tags { display: flex; flex-wrap: wrap; gap: 3px; }
.tag { font-size: 10px; padding: 1px 5px; border-radius: 3px; white-space: nowrap; }
.tag-phase { background: #1d3244; color: #79c0ff; }
.tag-intent { background: #2d1f0e; color: #ffa657; }
.tag-iter { background: #1e1e2e; color: #d2a8ff; }
.think { background: #161b22; border-left: 2px solid #7d8590; padding: 5px 8px; margin: 4px 0; color: #7d8590; white-space: pre-wrap; max-height: 80px; overflow-y: auto; font-size: 11px; }
.think.open { max-height: none; }
.meta-row { padding: 5px 12px; color: #7d8590; font-size: 11px; border-bottom: 1px solid #21262d; }

/* scrollbar */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }
</style>
</head>
<body>

<div id="stats">
  <div id="live"><span id="live-dot"></span>LLM PROXY</div>
  <div class="stat"><span class="stat-label">Calls</span><span class="stat-value" id="s-calls" style="color:#e6edf3">—</span></div>
  <div class="stat"><span class="stat-label">Cost</span><span class="stat-value" id="s-cost" style="color:#3fb950">—</span></div>
  <div class="stat"><span class="stat-label">Input Tokens</span><span class="stat-value" id="s-in" style="color:#79c0ff">—</span></div>
  <div class="stat"><span class="stat-label">Cached</span><span class="stat-value" id="s-cached" style="color:#56d364">—</span></div>
  <div class="stat"><span class="stat-label">Output Tokens</span><span class="stat-value" id="s-out" style="color:#ffa657">—</span></div>
  <div class="stat"><span class="stat-label">Avg Latency</span><span class="stat-value" id="s-lat" style="color:#d2a8ff">—</span></div>
  <div class="stat"><span class="stat-label">Jobs</span><span class="stat-value" id="s-jobs" style="color:#e6edf3">—</span></div>
</div>

<div id="main">
  <div id="jobs-panel">
    <h2>Jobs</h2>
    <div id="jobs-list"></div>
  </div>

  <div id="calls-area">
    <div class="col-header">
      <span>#</span><span>Job</span><span>Model</span><span>Tokens in / out</span>
      <span>Tags</span><span style="text-align:right">Cost</span><span style="text-align:right">Elapsed</span><span style="text-align:center">Status</span>
    </div>
    <div id="calls-list"></div>

    <div id="detail" class="hidden">
      <div id="detail-hdr">
        <span id="detail-title"></span>
        <button id="detail-close" onclick="closeDetail()">✕</button>
      </div>
      <div id="detail-body">
        <div class="dpane">
          <div class="dpane-hdr">Request</div>
          <div class="dpane-body" id="req-body"></div>
        </div>
        <div class="dpane">
          <div class="dpane-hdr">Response</div>
          <div class="dpane-body" id="resp-body"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let allCalls = [], jobMap = {}, activeJob = null, selId = null, lastId = 0;

const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmtM = n => n >= 1e6 ? (n/1e6).toFixed(2)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'K' : String(n);
const fmtC = (n, d=4) => '$' + n.toFixed(d);
const fmtC6 = n => fmtC(n, 6);

async function init() {
  const r = await fetch('/api/calls');
  allCalls = await r.json();
  if (allCalls.length) lastId = Math.max(...allCalls.map(c => c.global_id));
  await loadJobs();
  render();
  setInterval(poll, 2000);
}

async function loadJobs() {
  const r = await fetch('/api/jobs');
  const data = await r.json();
  jobMap = {};
  data.forEach(j => jobMap[j.job_name] = j);
  renderJobs(data);
}

async function poll() {
  const r = await fetch('/api/calls?after=' + lastId);
  const fresh = await r.json();
  if (!fresh.length) return;
  allCalls = allCalls.concat(fresh);
  lastId = Math.max(...allCalls.map(c => c.global_id));
  await loadJobs();
  render();
}

function visible() {
  return activeJob ? allCalls.filter(c => c.job_name === activeJob) : allCalls;
}

function render() {
  updateStats();
  renderCalls();
}

function updateStats() {
  const calls = visible();
  document.getElementById('s-calls').textContent = calls.length.toLocaleString();
  document.getElementById('s-cost').textContent = fmtC(calls.reduce((s,c)=>s+c.cost_usd,0));
  const totalIn = calls.reduce((s,c)=>s+c.input_tokens,0);
  const totalCached = calls.reduce((s,c)=>s+(c.cached_tokens||0),0);
  document.getElementById('s-in').textContent = fmtM(totalIn);
  document.getElementById('s-cached').textContent = totalIn ? fmtM(totalCached) + ' (' + Math.round(totalCached/totalIn*100) + '%)' : '—';
  document.getElementById('s-out').textContent = fmtM(calls.reduce((s,c)=>s+c.output_tokens,0));
  const avg = calls.length ? calls.reduce((s,c)=>s+c.elapsed_s,0)/calls.length : 0;
  document.getElementById('s-lat').textContent = avg.toFixed(1)+'s';
  document.getElementById('s-jobs').textContent = Object.keys(jobMap).length;
}

function renderJobs(data) {
  const el = document.getElementById('jobs-list');
  el.innerHTML = '';

  const allDiv = mkJobRow('All Jobs',
    fmtC(data.reduce((s,j)=>s+j.total_cost_usd,0)),
    data.reduce((s,j)=>s+j.total_calls,0) + ' calls',
    activeJob === null
  );
  allDiv.onclick = () => { activeJob = null; renderJobs(data); render(); };
  el.appendChild(allDiv);

  [...data].sort((a,b)=>b.total_cost_usd-a.total_cost_usd).forEach(j => {
    const cachePct = j.total_cached_tokens && j.total_input_tokens ? ' · '+Math.round(j.total_cached_tokens/j.total_input_tokens*100)+'% cached' : '';
    const f = j.flags || {};
    const warnings = [];
    if (f.expensive_low_output) warnings.push(f.expensive_low_output+' expensive/low-out');
    if (f.zero_output_calls) warnings.push(f.zero_output_calls+' empty');
    if (f.error_calls) warnings.push(f.error_calls+' errors');
    if (f.uncached_claude_input_tokens > 50000) warnings.push(fmtM(f.uncached_claude_input_tokens)+' uncached Claude');
    const warnStr = warnings.length ? ' · <span style="color:#f85149">'+warnings.join(', ')+'</span>' : '';
    const div = mkJobRow(j.job_name, fmtC(j.total_cost_usd), j.total_calls+' calls · '+fmtM(j.total_input_tokens)+' in'+cachePct+warnStr, activeJob === j.job_name);
    div.onclick = () => { activeJob = j.job_name; renderJobs(data); render(); };
    el.appendChild(div);
  });
}

function mkJobRow(name, cost, meta, active) {
  const d = document.createElement('div');
  d.className = 'job-item' + (active ? ' active' : '');
  d.innerHTML = `<div class="job-name">${esc(name)}</div><div class="job-meta"><span class="job-cost">${cost}</span> · ${meta}</div>`;
  return d;
}

function renderCalls() {
  const calls = visible().slice().reverse();
  const maxIn = calls.reduce((m,c) => Math.max(m, c.input_tokens), 1);
  const el = document.getElementById('calls-list');
  el.innerHTML = '';
  calls.forEach(c => {
    const row = document.createElement('div');
    row.className = 'call-row' + (selId === c.global_id ? ' selected' : '');
    const pct = Math.round(c.input_tokens / maxIn * 100);
    const ec = c.elapsed_s < 5 ? '#3fb950' : c.elapsed_s < 15 ? '#e3b341' : '#f85149';
    const sc = (c.status_code === 200 || !c.status_code) ? 'ok' : 'err';
    const tags = [
      c.iteration != null ? `<span class="tag tag-iter">i${esc(c.iteration)}</span>` : '',
      c.phase  ? `<span class="tag tag-phase">${esc(c.phase)}</span>` : '',
      c.intent ? `<span class="tag tag-intent">${esc(c.intent)}</span>` : '',
    ].filter(Boolean).join('');
    row.innerHTML = `
      <span class="c-id">#${c.global_id}</span>
      <span class="c-job" title="${esc(c.job_name)}">${esc(c.job_name)}</span>
      <span class="c-model" title="${esc(c.model)}">${esc(c.model)}</span>
      <span class="bar-wrap"><span class="c-in">${fmtM(c.input_tokens)}${c.cached_tokens ? '<span style="color:#56d364;font-size:10px" title="'+c.cached_tokens+' cached"> ('+Math.round(c.cached_tokens/c.input_tokens*100)+'%c)</span>':''}</span><span style="color:#30363d">/</span><span class="c-out">${fmtM(c.output_tokens)}</span><span class="tbar"><span class="tbar-fill" style="width:${pct}%"></span></span></span>
      <span class="tags">${tags || '<span style="color:#30363d">—</span>'}</span>
      <span class="c-cost">${fmtC6(c.cost_usd)}</span>
      <span class="c-elapsed" style="color:${ec}">${c.elapsed_s}s</span>
      <span class="c-status ${sc}">${c.status_code || '—'}</span>`;
    row.onclick = () => openDetail(c);
    el.appendChild(row);
  });
}

async function openDetail(call) {
  selId = call.global_id;
  renderCalls();
  const tags = [call.phase, call.intent, call.iteration != null ? `i${call.iteration}` : null].filter(Boolean).join(' · ');
  document.getElementById('detail-title').textContent =
    `#${call.global_id} · ${call.job_name} · ${call.model}${tags ? ' · '+tags : ''} · ${fmtC6(call.cost_usd)} · ${call.elapsed_s}s`;
  document.getElementById('detail').classList.remove('hidden');
  document.getElementById('req-body').innerHTML = '<div class="meta-row">Loading…</div>';
  document.getElementById('resp-body').innerHTML = '<div class="meta-row">Loading…</div>';

  const r = await fetch(`/api/call/${call.global_id}`);
  const data = await r.json();
  renderReq(data.request);
  renderResp(data.response);
}

function closeDetail() {
  selId = null;
  document.getElementById('detail').classList.add('hidden');
  renderCalls();
}

function renderReq(req) {
  const el = document.getElementById('req-body');
  if (!req) { el.innerHTML = '<div class="meta-row">No data</div>'; return; }
  let html = `<div class="meta-row"><span style="color:#d2a8ff">${esc(req.model||'—')}</span></div>`;
  (req.messages || []).forEach((m, i) => {
    html += msgHtml(m, i, 'q');
  });
  el.innerHTML = html;
}

function renderResp(resp) {
  const el = document.getElementById('resp-body');
  if (!resp) { el.innerHTML = '<div class="meta-row">No data</div>'; return; }

  let html = '';
  const u = resp.usage || {};
  if (Object.keys(u).length) {
    html += `<div class="meta-row">${Object.entries(u).map(([k,v])=>`<span style="color:#7d8590">${k}:</span> ${v}`).join(' · ')}</div>`;
  }

  (resp.choices || []).forEach((ch, ci) => {
    const msg = ch.message || {};
    let content = msg.content || '';

    // think block
    const thinkM = content.match(/<think>([\s\S]*?)<\/think>/);
    if (thinkM) {
      const tid = `think-${ci}`;
      html += `<div class="msg"><div class="msg-role r-meta">reasoning</div><div class="think" id="${tid}">${esc(thinkM[1].trim())}</div>${thinkM[1].length > 300 ? `<span class="more-btn" onclick="tog('${tid}','think')">show more</span>` : ''}</div>`;
      content = content.replace(/<think>[\s\S]*?<\/think>\s*/, '');
    }

    if (content.trim()) {
      const id = `rc-${ci}`;
      const long = content.length > 500;
      html += `<div class="msg"><div class="msg-role r-assistant">${esc(msg.role||'assistant')}</div><div class="msg-body${long?'':' open'}" id="${id}">${esc(content.trim())}</div>${long?`<span class="more-btn" onclick="tog('${id}','msg-body')">show more (${content.length.toLocaleString()} chars)</span>`:''}</div>`;
    }

    (msg.tool_calls || []).forEach((tc, ti) => {
      const fn = tc.function || {};
      let args = fn.arguments || '';
      try { args = JSON.stringify(JSON.parse(args), null, 2); } catch(e) {}
      const aid = `tc-${ci}-${ti}`;
      const long = args.length > 300;
      html += `<div class="msg"><div class="msg-role r-tool">tool call</div><div class="tool-call"><div class="tc-name">${esc(fn.name||'?')}</div><div class="tc-args${long?'':' open'}" id="${aid}">${esc(args)}</div>${long?`<span class="more-btn" onclick="tog('${aid}','tc-args')">show more</span>`:''}</div></div>`;
    });

    if (ch.finish_reason) {
      html += `<div class="meta-row">finish: <span style="color:#e6edf3">${esc(ch.finish_reason)}</span></div>`;
    }
  });

  el.innerHTML = html;
}

function msgHtml(m, i, prefix) {
  const role = m.role || 'unknown';
  const cls = {system:'r-system',user:'r-user',assistant:'r-assistant',tool:'r-tool'}[role]||'r-meta';
  let content = '';
  if (typeof m.content === 'string') {
    content = m.content;
  } else if (Array.isArray(m.content)) {
    content = m.content.map(b => {
      if (b.type === 'text') return b.text || '';
      if (b.type === 'tool_result') return `[tool_result id=${b.tool_use_id}]\n${JSON.stringify(b.content, null, 2)}`;
      if (b.type === 'tool_use') return `[tool_use name=${b.name}]\n${JSON.stringify(b.input, null, 2)}`;
      return JSON.stringify(b, null, 2);
    }).join('\n');
  } else {
    content = JSON.stringify(m.content, null, 2);
  }
  const id = `${prefix}-msg-${i}`;
  const long = content.length > 500;
  return `<div class="msg"><div class="msg-role ${cls}">${role}</div><div class="msg-body${long?'':' open'}" id="${id}">${esc(content)}</div>${long?`<span class="more-btn" onclick="tog('${id}','msg-body')">show more (${content.length.toLocaleString()} chars)</span>`:''}</div>`;
}

function tog(id, cls) {
  const el = document.getElementById(id);
  const btn = el.nextElementSibling;
  if (el.classList.contains('open')) {
    el.classList.remove('open');
    if (btn) btn.textContent = 'show more' + (btn.textContent.includes('chars') ? btn.textContent.replace(/show (more|less)/, '') : '');
  } else {
    el.classList.add('open');
    if (btn) btn.textContent = btn.textContent.replace('show more', 'show less');
  }
}

init();
</script>
</body>
</html>
"""


class ViewerHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send(200, "text/html", HTML.encode())
        elif path == "/api/calls":
            after = int(qs.get("after", ["0"])[0])
            self._json(read_all_calls(after))
        elif path == "/api/jobs":
            self._json(read_jobs())
        elif path.startswith("/api/jobs/"):
            name = path[len("/api/jobs/"):]
            jobs = read_jobs()
            job = next((j for j in jobs if j.get("job_name") == name), None)
            if job:
                self._json(job)
            else:
                self._send(404, "application/json", b'{"error":"not found"}')
        elif path.startswith("/api/call/"):
            try:
                gid = int(path[len("/api/call/"):])
            except ValueError:
                self._send(400, "application/json", b'{"error":"bad id"}')
                return
            calls = read_all_calls()
            call = next((c for c in calls if c.get("global_id") == gid), None)
            if call:
                self._json(read_call_detail(call["job_name"], call["call_id"]))
            else:
                self._send(404, "application/json", b'{"error":"not found"}')
        else:
            self._send(404, "application/json", b'{"error":"not found"}')

    def _json(self, data):
        body = json.dumps(data).encode()
        self._send(200, "application/json", body)

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ViewerHandler)
    print(f"Viewer: http://localhost:{PORT}", flush=True)
    print(f"Logs:   {LOG_DIR}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
