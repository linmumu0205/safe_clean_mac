#!/usr/bin/env python3
"""
Safe Mac Cleaner Web UI (no third-party dependencies)

- Local-only web app: listens on 127.0.0.1
- Scan and cleanup with strict allowlist
- Cleanup moves files into ~/.Trash (never permanent delete)
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

HOME = Path.home()
TRASH_DIR = HOME / ".Trash"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

SCAN_PROFILES = {
    "standard": {
        "label": "Standard",
        "days": 30,
        "include_container_caches": False,
        "include_xcode": False,
        "max_candidates": 50000,
        "progress_every": 1000,
    },
    "focused": {
        "label": "Focused",
        "days": 14,
        "include_container_caches": True,
        "include_xcode": True,
        "max_candidates": 80000,
        "progress_every": 1000,
    },
    "developer": {
        "label": "Developer",
        "days": 7,
        "include_container_caches": False,
        "include_xcode": True,
        "max_candidates": 100000,
        "progress_every": 1000,
    },
}


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def human_size(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(num)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.2f} {u}"
        x /= 1024
    return f"{num} B"


@dataclass
class ScanConfig:
    profile: str = "standard"
    days: int = 30
    include_container_caches: bool = False
    include_xcode: bool = False
    max_candidates: int = 0
    progress_every: int = 1000


@dataclass
class ScanResult:
    id: str
    config: ScanConfig
    created_at: str
    status: str = "running"
    phase: str = "scan"
    current_root: str = ""
    scanned_files: int = 0
    scanned_bytes: int = 0
    finished_roots: List[Dict[str, str]] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    top_files: List[Tuple[str, int]] = field(default_factory=list)
    dir_sizes: Dict[str, int] = field(default_factory=dict)
    error: str = ""
    stop_reason: str = ""


class CleanerPolicy:
    def __init__(self, cfg: ScanConfig):
        self.cfg = cfg
        self.allowed_roots = self._build_allowed_roots()

    def _build_allowed_roots(self) -> List[Path]:
        roots = [
            HOME / "Library" / "Caches",
            HOME / "Library" / "Logs",
            HOME / "Library" / "Application Support" / "CrashReporter",
            HOME / "Library" / "Developer" / "CoreSimulator" / "Caches",
        ]
        if self.cfg.include_container_caches:
            containers = HOME / "Library" / "Containers"
            if containers.is_dir():
                try:
                    for child in containers.iterdir():
                        cache_dir = child / "Data" / "Library" / "Caches"
                        roots.append(cache_dir)
                except OSError:
                    pass
        if self.cfg.include_xcode:
            roots.append(HOME / "Library" / "Developer" / "Xcode" / "DerivedData")

        out = []
        for p in roots:
            try:
                if p.is_dir():
                    out.append(p.resolve())
            except OSError:
                continue
        return out

    def is_under_allowed(self, p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        for root in self.allowed_roots:
            if rp == root or root in rp.parents:
                return True
        return False


def build_scan_config(body: dict) -> ScanConfig:
    profile = str(body.get("profile", "standard")).strip() or "standard"
    if profile not in SCAN_PROFILES:
        raise ValueError(f"unknown profile: {profile}")

    preset = SCAN_PROFILES[profile]

    cfg = ScanConfig(
        profile=profile,
        days=int(body.get("days", preset["days"])),
        include_container_caches=bool(
            body.get("include_container_caches", preset["include_container_caches"])
        ),
        include_xcode=bool(body.get("include_xcode", preset["include_xcode"])),
        max_candidates=max(0, int(body.get("max_candidates", preset["max_candidates"]))),
        progress_every=max(0, int(body.get("progress_every", preset["progress_every"]))),
    )
    if cfg.days < 0:
        raise ValueError("days must be >= 0")
    return cfg


def update_top_files(top_files: List[Tuple[str, int]], path: str, size: int, limit: int = 20) -> None:
    top_files.append((path, size))
    top_files.sort(key=lambda x: x[1], reverse=True)
    if len(top_files) > limit:
        del top_files[limit:]


def top_directories(dir_sizes: Dict[str, int], limit: int = 12) -> List[Tuple[str, int]]:
    return sorted(dir_sizes.items(), key=lambda x: x[1], reverse=True)[:limit]


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, ScanResult] = {}
        self._lock = threading.Lock()

    def put(self, job: ScanResult) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[ScanResult]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, n: int = 20) -> List[ScanResult]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda x: x.created_at, reverse=True)
        return jobs[:n]


STORE = JobStore()


def scan_worker(job_id: str) -> None:
    job = STORE.get(job_id)
    if not job:
        return

    cfg = job.config
    policy = CleanerPolicy(cfg)
    if not policy.allowed_roots:
        job.status = "done"
        job.stop_reason = "No scan roots found"
        return

    cutoff = time.time() - (cfg.days * 86400)

    try:
        for root in policy.allowed_roots:
            job.current_root = str(root)
            root_count = 0
            root_bytes = 0

            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    f = Path(dirpath) / name
                    try:
                        st = f.stat()
                    except OSError:
                        continue

                    if st.st_mtime > cutoff:
                        continue

                    if not policy.is_under_allowed(f):
                        continue

                    size = st.st_size
                    path_str = str(f)
                    job.scanned_files += 1
                    job.scanned_bytes += size
                    root_count += 1
                    root_bytes += size
                    job.candidates.append(path_str)

                    parent = str(f.parent)
                    job.dir_sizes[parent] = job.dir_sizes.get(parent, 0) + size
                    update_top_files(job.top_files, path_str, size)

                    if cfg.progress_every > 0 and job.scanned_files % cfg.progress_every == 0:
                        pass

                    if cfg.max_candidates > 0 and job.scanned_files >= cfg.max_candidates:
                        job.stop_reason = f"Reached max_candidates={cfg.max_candidates}"
                        job.finished_roots.append(
                            {
                                "root": str(root),
                                "files": str(root_count),
                                "bytes": str(root_bytes),
                            }
                        )
                        job.status = "done"
                        job.phase = "idle"
                        return

            job.finished_roots.append(
                {
                    "root": str(root),
                    "files": str(root_count),
                    "bytes": str(root_bytes),
                }
            )

        job.status = "done"
        job.phase = "idle"

    except Exception as exc:  # pylint: disable=broad-except
        job.status = "error"
        job.error = str(exc)


class CleanerExecutor:
    @staticmethod
    def apply(scan: ScanResult, confirm_text: str) -> Dict[str, str]:
        if confirm_text != "MOVE_TO_TRASH":
            raise ValueError("Confirmation text mismatch. Type MOVE_TO_TRASH to continue.")

        session = f"safe-clean-web-{now_ts()}"
        trash_session = TRASH_DIR / session
        log_file = HOME / f"{session}.log"

        moved = 0
        failed = 0

        for p_str in scan.candidates:
            p = Path(p_str)
            if not p.exists() or not p.is_file():
                continue
            if HOME not in p.resolve().parents and p.resolve() != HOME:
                failed += 1
                continue

            rel = p.relative_to(HOME)
            target = trash_session / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(p), str(target))
                moved += 1
            except (OSError, shutil.Error):
                failed += 1

        line = (
            f"session={session} mode=apply days={scan.config.days} moved={moved} "
            f"failed={failed} estimated_bytes={scan.scanned_bytes}\n"
        )
        with open(log_file, "a", encoding="utf-8") as fp:
            fp.write(line)

        return {
            "session": session,
            "moved": str(moved),
            "failed": str(failed),
            "trash_path": str(trash_session),
            "log_file": str(log_file),
        }


def parse_json_body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        length = 0
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def html_response(handler: BaseHTTPRequestHandler, html: str, status: int = 200) -> None:
    data = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def build_index_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Safe Mac Cleaner</title>
  <style>
    :root {
      --bg: #f3f4ef;
      --card: #ffffff;
      --ink: #1f2937;
      --sub: #6b7280;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --warn: #b45309;
      --line: #e5e7eb;
    }
    body {
      margin: 0;
      background:
        linear-gradient(135deg, rgba(15,118,110,.08), transparent 34%),
        linear-gradient(225deg, rgba(180,83,9,.08), transparent 32%),
        var(--bg);
      color: var(--ink);
      font-family: "SF Pro Text", "PingFang SC", "Helvetica Neue", sans-serif;
    }
    .wrap { max-width: 980px; margin: 24px auto; padding: 0 16px 40px; }
    .hero { margin-bottom: 14px; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    .hint { color: var(--sub); }
    .grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.04);
    }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    label { font-size: 14px; color: var(--sub); }
    input[type="number"], input[type="text"] {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 9px;
      font-size: 14px;
    }
    button {
      border: 0;
      border-radius: 9px;
      background: var(--accent);
      color: #fff;
      padding: 8px 12px;
      cursor: pointer;
      font-weight: 600;
    }
    button.secondary { background: #334155; }
    button.profile { background: #475569; }
    button.profile.active { background: var(--accent-2); }
    button.warn { background: #b91c1c; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .small { font-size: 13px; color: var(--sub); }
    .stat { font-size: 14px; margin: 4px 0; }
    .ok { color: #166534; }
    .warning { color: var(--warn); }
    .table { width: 100%; border-collapse: collapse; }
    .table th, .table td { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; font-size: 13px; }
    .table td:first-child { word-break: break-word; }
    .pill { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 12px; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>Safe Mac Cleaner</h1>
      <div class="hint">默认只扫描，不会删除。执行清理时仅移动到废纸篓，确认词必须为 <span class="mono">MOVE_TO_TRASH</span>。</div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>1) 扫描参数</h3>
        <div class="toolbar">
          <button class="profile active" data-profile="standard">标准扫描</button>
          <button class="profile" data-profile="focused">重点扫描</button>
          <button class="profile" data-profile="developer">开发者扫描</button>
        </div>
        <div class="row">
          <label>天数阈值 <input id="days" type="number" min="0" value="30" /></label>
          <label>进度频率 <input id="progress" type="number" min="0" value="1000" /></label>
          <label>最大候选 <input id="max" type="number" min="0" value="50000" /></label>
          <label><input id="containers" type="checkbox" /> 包含 Container 缓存</label>
          <label><input id="xcode" type="checkbox" /> 包含 Xcode DerivedData</label>
          <button id="scanBtn">开始扫描</button>
        </div>
        <div id="scanState" class="small" style="margin-top:8px;">尚未开始</div>
      </div>

      <div class="card">
        <h3>2) 扫描结果</h3>
        <div id="summary" class="stat">无</div>
        <div id="roots" class="small"></div>
        <h4>Top 目录</h4>
        <table class="table" id="topDirsTable">
          <thead><tr><th>目录</th><th>大小</th></tr></thead>
          <tbody></tbody>
        </table>
        <h4>Top 大文件</h4>
        <table class="table" id="topFilesTable">
          <thead><tr><th>文件</th><th>大小</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="card">
        <h3>3) 执行清理（移动到废纸篓）</h3>
        <div class="small">输入确认词后才会执行：<span class="mono">MOVE_TO_TRASH</span></div>
        <div class="row" style="margin-top:8px;">
          <input id="confirm" type="text" placeholder="请输入确认词" />
          <button id="applyBtn" class="warn" disabled>执行清理</button>
        </div>
        <div id="applyResult" class="small" style="margin-top:8px;"></div>
      </div>

      <div class="card">
        <h3>最近任务</h3>
        <button id="refreshJobs" class="secondary">刷新</button>
        <div id="jobs" class="small" style="margin-top:8px;"></div>
      </div>
    </div>
  </div>

<script>
const state = { currentJobId: null, pollTimer: null, currentStatus: null };
const presets = {
  standard: { days: 30, progress_every: 1000, max_candidates: 50000, containers: false, xcode: false },
  focused: { days: 14, progress_every: 1000, max_candidates: 80000, containers: true, xcode: true },
  developer: { days: 7, progress_every: 1000, max_candidates: 100000, containers: false, xcode: true },
};
let activeProfile = 'standard';

function fmtBytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let x = Number(n || 0), i = 0;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(2)} ${units[i]}`;
}

function setScanState(text, cls='') {
  const el = document.getElementById('scanState');
  el.textContent = text;
  el.className = `small ${cls}`;
}

function applyPreset(name) {
  const preset = presets[name] || presets.standard;
  activeProfile = name in presets ? name : 'standard';
  document.getElementById('days').value = preset.days;
  document.getElementById('progress').value = preset.progress_every;
  document.getElementById('max').value = preset.max_candidates;
  document.getElementById('containers').checked = preset.containers;
  document.getElementById('xcode').checked = preset.xcode;
  document.querySelectorAll('button.profile').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.profile === activeProfile);
  });
}

function updateSummary(job) {
  const summary = document.getElementById('summary');
  summary.textContent = `候选文件 ${job.scanned_files} 个，估算 ${fmtBytes(job.scanned_bytes)}，状态 ${job.status}`;

  const roots = document.getElementById('roots');
  const parts = (job.finished_roots || []).map(r => `${r.root}: ${r.files} files / ${fmtBytes(r.bytes)}`);
  roots.innerHTML = parts.map(p => `<div>${p}</div>`).join('') || '暂无';

  const dirsBody = document.querySelector('#topDirsTable tbody');
  dirsBody.innerHTML = '';
  (job.top_dirs || []).forEach(([p, s]) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="mono">${p}</td><td>${fmtBytes(s)}</td>`;
    dirsBody.appendChild(tr);
  });

  const tbody = document.querySelector('#topFilesTable tbody');
  tbody.innerHTML = '';
  (job.top_files || []).forEach(([p, s]) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="mono">${p}</td><td>${fmtBytes(s)}</td>`;
    tbody.appendChild(tr);
  });

  const applyBtn = document.getElementById('applyBtn');
  applyBtn.disabled = !(job.status === 'done' && job.scanned_files > 0);
}

async function startScan() {
  const body = {
    profile: activeProfile,
    days: Number(document.getElementById('days').value || 30),
    progress_every: Number(document.getElementById('progress').value || 1000),
    max_candidates: Number(document.getElementById('max').value || 0),
    include_container_caches: document.getElementById('containers').checked,
    include_xcode: document.getElementById('xcode').checked,
  };

  const r = await fetch('/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) {
    setScanState(`启动失败: ${data.error || 'unknown'}`, 'warning');
    return;
  }
  state.currentJobId = data.job_id;
  setScanState(`扫描已启动: ${data.job_id}`);
  pollJob();
}

async function pollJob() {
  if (!state.currentJobId) return;
  if (state.pollTimer) clearInterval(state.pollTimer);

  const tick = async () => {
    const r = await fetch(`/api/scan/${state.currentJobId}`);
    const data = await r.json();
    if (!r.ok) {
      setScanState(`读取任务失败: ${data.error || 'unknown'}`, 'warning');
      return;
    }
    state.currentStatus = data;
    const base = `扫描中: ${data.current_root || '-'} | ${data.scanned_files} files | ${fmtBytes(data.scanned_bytes)}`;
    if (data.status === 'running') {
      setScanState(base);
    } else if (data.status === 'done') {
      setScanState(`扫描完成: ${data.scanned_files} files, ${fmtBytes(data.scanned_bytes)} ${data.stop_reason ? '(' + data.stop_reason + ')' : ''}`, 'ok');
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    } else {
      setScanState(`任务异常: ${data.error || 'unknown'}`, 'warning');
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    updateSummary(data);
  };

  await tick();
  state.pollTimer = setInterval(tick, 1200);
}

async function applyClean() {
  if (!state.currentJobId) return;
  const confirmText = document.getElementById('confirm').value || '';

  const r = await fetch('/api/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: state.currentJobId, confirm_text: confirmText })
  });
  const data = await r.json();
  const out = document.getElementById('applyResult');
  if (!r.ok) {
    out.innerHTML = `<span class="warning">执行失败: ${data.error || 'unknown'}</span>`;
    return;
  }
  out.innerHTML = `完成: moved=${data.moved}, failed=${data.failed}<br/>Trash: <span class="mono">${data.trash_path}</span><br/>Log: <span class="mono">${data.log_file}</span>`;
}

async function refreshJobs() {
  const r = await fetch('/api/jobs');
  const data = await r.json();
  const el = document.getElementById('jobs');
  if (!r.ok) {
    el.textContent = '读取失败';
    return;
  }
  const arr = data.jobs || [];
  if (arr.length === 0) {
    el.textContent = '暂无';
    return;
  }
  el.innerHTML = arr.map(j => `<div><span class="pill">${j.profile || 'standard'}</span> <span class="pill">${j.status}</span> <span class="mono">${j.id}</span> ${j.scanned_files} files / ${fmtBytes(j.scanned_bytes)} (${j.created_at})</div>`).join('');
}

document.getElementById('scanBtn').addEventListener('click', startScan);
document.getElementById('applyBtn').addEventListener('click', applyClean);
document.getElementById('refreshJobs').addEventListener('click', refreshJobs);
document.querySelectorAll('button.profile').forEach(btn => {
  btn.addEventListener('click', () => applyPreset(btn.dataset.profile));
});
applyPreset('standard');
refreshJobs();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            html_response(self, build_index_html())
            return

        if path == "/api/jobs":
            jobs = []
            for j in STORE.list_recent():
                jobs.append(
                    {
                        "id": j.id,
                        "created_at": j.created_at,
                        "profile": j.config.profile,
                        "status": j.status,
                        "scanned_files": j.scanned_files,
                        "scanned_bytes": j.scanned_bytes,
                    }
                )
            json_response(self, {"jobs": jobs})
            return

        if path.startswith("/api/scan/"):
            job_id = path.rsplit("/", 1)[-1]
            job = STORE.get(job_id)
            if not job:
                json_response(self, {"error": "job not found"}, status=404)
                return

            top_files = [[p, s] for p, s in job.top_files]
            top_dirs = [[p, s] for p, s in top_directories(job.dir_sizes)]
            payload = {
                "id": job.id,
                "created_at": job.created_at,
                "profile": job.config.profile,
                "status": job.status,
                "phase": job.phase,
                "current_root": job.current_root,
                "scanned_files": job.scanned_files,
                "scanned_bytes": job.scanned_bytes,
                "finished_roots": job.finished_roots,
                "top_files": top_files,
                "top_dirs": top_dirs,
                "stop_reason": job.stop_reason,
                "error": job.error,
            }
            json_response(self, payload)
            return

        json_response(self, {"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/scan":
            body = parse_json_body(self)
            try:
                cfg = build_scan_config(body)
            except (TypeError, ValueError) as exc:
                json_response(self, {"error": f"invalid params: {exc}"}, status=400)
                return

            job_id = uuid.uuid4().hex[:12]
            job = ScanResult(
                id=job_id,
                config=cfg,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            STORE.put(job)
            t = threading.Thread(target=scan_worker, args=(job_id,), daemon=True)
            t.start()

            json_response(self, {"job_id": job_id})
            return

        if parsed.path == "/api/apply":
            body = parse_json_body(self)
            job_id = str(body.get("job_id", "")).strip()
            confirm_text = str(body.get("confirm_text", "")).strip()

            job = STORE.get(job_id)
            if not job:
                json_response(self, {"error": "job not found"}, status=404)
                return
            if job.status != "done":
                json_response(self, {"error": "scan not finished"}, status=400)
                return
            if not job.candidates:
                json_response(self, {"error": "no candidates to clean"}, status=400)
                return

            try:
                res = CleanerExecutor.apply(job, confirm_text)
                json_response(self, res)
            except ValueError as exc:
                json_response(self, {"error": str(exc)}, status=400)
            except Exception as exc:  # pylint: disable=broad-except
                json_response(self, {"error": f"apply failed: {exc}"}, status=500)
            return

        json_response(self, {"error": "not found"}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    host = os.environ.get("SAFE_CLEAN_HOST", DEFAULT_HOST)
    port = int(os.environ.get("SAFE_CLEAN_PORT", str(DEFAULT_PORT)))

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[INFO] Safe Clean Web running at http://{host}:{port}")
    print("[INFO] Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
