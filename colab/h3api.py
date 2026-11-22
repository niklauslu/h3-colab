#!/usr/bin/env python3
"""H3 契约外壳（v0.3 plan v2）— agent 契约 API → ComfyUI 内核翻译层。纯 stdlib。

架构（docs/plan-nocomfy.md v2）：ComfyUI 是唯一能跑 8-step 蒸馏权重的内核（实测结论），
外壳把它封在 VM 内部——agent/控制台只面对本契约，永不再接触节点图。

  127.0.0.1:8188  本外壳（对外唯一口，SSH 隧道直达）
  127.0.0.1:8189  ComfyUI（内部，由 bootstrap 守护循环看护）

契约（与归档的 server_h3.py 完全一致，agent CLI 零改动）：
  GET  /health                  200 {"ok","phase"} / 503（守护探活；phase=ready 须 ComfyUI 可达）
  GET  /state                   全量快照（阶段/时间戳/GPU/队列/job 历史）
  GET  /status?job_id=..        单 job（agent 主轮询点）
  GET  /result?job_id=..        产出文件列表
  GET  /files?job_id=..&name=.. 产出字节（代理 ComfyUI /view）
  GET  /events?since=N          SSE 事件流
  POST /generate {...}          202 {"job_id"}；模板注入在壳内完成
  POST /cancel?job_id=..        pending 删队 / running 发 /interrupt
  POST /upload?name=..          素材落 ComfyUI input 目录（LoadImage 直接引用）

/generate 请求体（同 server_h3.py）：
  {"mode":"t2v|fl2v|ref2v","prompt":"...","seed":42,"seconds":5,
   "width":..,"height":..,"name":"demo",
   "keyframes":[{"path":<input文件名>,"index":0|-1}],      # fl2v: 0=首帧 -1=尾帧
   "references":[{"type":"image","path":<input文件名>}]}    # ref2v: 模板现支持 2 张

节点寻址全部封装在 TEMPLATES 注入表里（来自 v0.2 实测工作流）。
H3_DRYRUN=1 时内置假 ComfyUI，本地冒烟全链路（不碰 GPU）。
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.environ.get("H3_ROOT", "/content/h3")
CUI_INPUT = os.environ.get("H3_CUI_INPUT", "/content/ComfyUI/input")
COMFY = os.environ.get("H3_COMFY", "http://127.0.0.1:8189")
DRYRUN = os.environ.get("H3_DRYRUN") == "1"
os.makedirs(ROOT, exist_ok=True)
SERVER_LOG = f"{ROOT}/h3api.log"
SESSION_JSON = f"{ROOT}/session.json"
WF_DIR = f"{ROOT}/workflows"

CFG = {"port": 8188, "auth_token": "", "fps": 24, "seconds": 5}
try:
    with open(f"{ROOT}/config.json") as f:
        CFG.update(json.load(f))
except Exception:
    pass
PORT = int(CFG["port"])

# ---- 模板注入表（节点寻址封装点；来自 v0.2 实测工作流，勿外泄给 agent） ----
# 按档案选表（2026-11-22 D2' 重排：Omni/Pika 单档案分会话，v2 基线裁撤）——
# agent 契约不变（仍只传 mode），选哪张模板由 config.json 的 weights_profile 决定
_T_V2 = {
    "t2v": {
        "file": "t2v_api.json",
        "inject": {"prompt": ("5", "text"), "seed": ("9", "seed"),
                   "steps": ("9", "steps"), "width": ("7", "width"),
                   "height": ("7", "height"), "length": ("7", "length"),
                   "prefix": ("13", "filename_prefix")},
        "length_default": 124,
    },
    "fl2v": {
        "file": "fl2v_api.json",
        "inject": {"prompt": ("9", "prompt"), "seed": ("12", "seed"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "first": ("5", "image"), "last": ("6", "image"),
                   "prefix": ("16", "filename_prefix")},
    },
    "ref2v": {
        "file": "ref2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("10", "seed"),
                   "ref0": ("5", "image"), "ref1": ("6", "image"),
                   "prefix": ("14", "filename_prefix")},
        "max_refs": 2,
    },
}
_T_PDD = {
    # PDD: steps 即 nfe, 官方合法 8/4, 节点要字符串（坑㉘）→ str_keys 转换
    "t2v": {
        "file": "t2v_pdd_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("9", "noise_seed"),
                   "steps": ("6", "nfe"), "width": ("7", "width"),
                   "height": ("7", "height"), "length": ("7", "length"),
                   "prefix": ("15", "filename_prefix")},
        "str_keys": ["steps"], "length_default": 124,
    },
}
_T_OMNI = {
    # OmniRef: euler 8步 cfg1 + SigmaShift(12/3), 与 v2 链同构, 仅换 DiT
    # 提示词建议 subject_definitions 结构（<Picture 1>=人物 <Picture 2>=场景）
    "ref2v": {
        "file": "omni_ref2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("10", "seed"),
                   "ref0": ("5", "image"), "ref1": ("6", "image"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "length": ("7", "length"), "prefix": ("14", "filename_prefix")},
        "max_refs": 2,
    },
}
_T_PIKA = {
    # Pika Flex: 6步 lcm + ManualSigmas(7值勿动), LoRA 强度 1.0 勿动, 禁叠 turbo
    "ref2v": {
        "file": "pika_ref2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("10", "noise_seed"),
                   "ref0": ("5", "image"), "ref1": ("6", "image"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "length": ("7", "length"), "prefix": ("17", "filename_prefix")},
        "max_refs": 2,
    },
}
TEMPLATES_BY_PROFILE = {
    "h3video": _T_V2,
    "h3pdd": _T_PDD,
    "h3omni": _T_OMNI,
    "h3pika": _T_PIKA,
    # zimage/qwen21t 生图档案走 ComfyUI 直连(不经外壳), 此表不覆盖
}
TEMPLATES = _T_V2  # 兜底(未知档案) = v2 全模式


def _profile_templates():
    return TEMPLATES_BY_PROFILE.get(CFG.get("weights_profile", "h3video"), TEMPLATES)

# ---------------------------------------------------------------- events/state

EVENTS = []
EV_LOCK = threading.Lock()
EV_COND = threading.Condition(EV_LOCK)
MAX_EVENTS = 2000

STATE = {
    "phase": "booting",           # booting→ready | degraded(comfy down) | failed
    "phase_detail": "",
    "error": None,
    "runtime": "comfyui-shell",
    "machine": {},
    "weights": {"done": [], "bytes": 0},
    "jobs": {},
    "session": {},
}
ST_LOCK = threading.Lock()


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def emit(etype, **data):
    with EV_LOCK:
        EVENTS.append({"id": len(EVENTS) + 1, "ts": now_iso(), "type": etype, **data})
        del EVENTS[:-MAX_EVENTS]
        EV_COND.notify_all()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(SERVER_LOG, "a") as f:
        f.write(line + "\n")


def _write_session_locked():
    with open(SESSION_JSON, "w") as f:
        json.dump({"session": STATE["session"], "phase": STATE["phase"],
                   "weights": STATE["weights"], "jobs": STATE["jobs"]},
                  f, ensure_ascii=False, indent=1)


def set_phase(phase, detail=""):
    with ST_LOCK:
        STATE["phase"] = phase
        STATE["phase_detail"] = detail
        if phase == "ready":
            STATE["session"].setdefault("ready_at", now_iso())
        _write_session_locked()
    emit("phase", phase=phase, detail=detail)


def sh_quiet(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()


def gpu_info():
    return {"raw": sh_quiet("nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader") or None}


def comfy_req(path, payload=None, timeout=15, method=None):
    """调内部 ComfyUI → (status, json/bytes)。DRYRUN 下走桩。"""
    if DRYRUN:
        return _fake_comfy(path, payload)
    url = f"{COMFY}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(url, data=data, headers=headers,
                               method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def comfy_json(path, payload=None, timeout=15):
    st, body = comfy_req(path, payload, timeout)
    try:
        return st, json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return st, None


def _weights_info():
    files, total = [], 0
    models_dir = f"{ROOT.rsplit('/h3', 1)[0]}/models"
    for base, _d, names in os.walk(models_dir):
        for n in names:
            p = os.path.join(base, n)
            if os.path.isfile(p) and (n.endswith(".safetensors") or n.endswith(".json")):
                files.append(n)
                if n.endswith(".safetensors"):
                    total += os.path.getsize(p)
    return {"done": sorted(files)[:40], "bytes": total}


class _FakeComfy:
    """DRYRUN 桩：模拟 /prompt /history /queue /interrupt /view /system_stats。"""

    def __init__(self):
        self.hist = {}
        self.seq = [0]

    def __call__(self, path, payload=None):
        if path == "/prompt" and payload:
            self.seq[0] += 1
            pid = f"fake-{self.seq[0]}"
            self.hist[pid] = {"status": {"status_str": "success"},
                              "outputs": {"13": {"videos": [{"filename": f"{pid}.mp4",
                                                             "subfolder": "", "type": "output"}]}}}
            self.hist[pid]["_ready_at"] = time.time() + 1.5
            return 200, json.dumps({"prompt_id": pid}).encode()
        if path.startswith("/history/"):
            pid = path.split("/")[-1]
            h = self.hist.get(pid)
            if h and time.time() < h["_ready_at"]:
                return 200, json.dumps({}).encode()
            return 200, json.dumps({pid: h} if h else {}).encode()
        if path == "/queue":
            return 200, json.dumps({"queue_running": [], "queue_pending": []}).encode()
        if path == "/system_stats":
            return 200, json.dumps({"devices": [{"vram_free": 1, "vram_total": 2}]}).encode()
        if path.startswith("/view"):
            return 200, b"FAKE"
        if path == "/interrupt":
            return 200, b"{}"
        return 404, b"{}"


_FAKE = _FakeComfy()


def _fake_comfy(path, payload=None):
    st, body = _FAKE(path, payload)
    return st, body


# ---------------------------------------------------------------- templates

def load_template(mode):
    t = _profile_templates().get(mode)
    if not t:
        raise ValueError(f"档案 {CFG.get('weights_profile', 'h3video')} 的 mode 须为 "
                         f"{list(_profile_templates())}, 收到 {mode!r}")
    path = os.path.join(WF_DIR, t["file"])
    if not os.path.exists(path):
        raise RuntimeError(f"模板缺失: {path}（bootstrap 应上传 workflows/）")
    with open(path) as f:
        return t, json.load(f)


def inject(tpl, wf, **vals):
    str_keys = set(tpl.get("str_keys") or [])
    for key, val in vals.items():
        if val is None:
            continue
        node, input_key = tpl["inject"][key]
        if node not in wf:
            raise RuntimeError(f"模板缺节点 {node}")
        if key in str_keys:  # 坑㉘: PDD nfe 等节点要字符串枚举
            val = str(val)
        wf[node]["inputs"][input_key] = val


def frames_for(seconds):
    return (round(float(seconds) * CFG["fps"]) // 4) * 4 + 4


# ---------------------------------------------------------------- jobs

JOBS = {}
JOB_SEQ = [0]
POLL_STOP = threading.Event()
LAST_CLIP_SEC = [None]


def new_job_id():
    with EV_LOCK:
        JOB_SEQ[0] += 1
    return f"j{JOB_SEQ[0]:04d}-{time.strftime('%H%M%S')}"


def submit_job(spec):
    mode = spec.get("mode", "t2v")
    tpl, wf = load_template(mode)
    prompt = (spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    seconds = float(spec.get("seconds", CFG["seconds"]))
    if not 4 <= seconds <= 15:
        raise ValueError(f"seconds 超出模型范围 4~15: {seconds}")
    job = {"id": new_job_id(), "mode": mode, "prompt": prompt,
           "seed": int(spec.get("seed", 42)), "seconds": seconds,
           "width": spec.get("width"), "height": spec.get("height"),
           "name": (spec.get("name") or "").strip() or None,
           "state": "queued", "submitted_at": now_iso(),
           "started_at": None, "finished_at": None, "elapsed_s": None,
           "progress": None, "error": None, "outputs": [],
           "prompt_id": None, "cancel_requested": False}
    job["_outdir"] = re.sub(r"[^\w\-.]+", "_", job["name"] or job["id"])

    # 注入（节点寻址封闭在壳内）
    vals = {"prompt": prompt, "seed": job["seed"], "steps": int(spec.get("steps", 8))}
    if mode == "t2v":
        vals["length"] = frames_for(seconds)
        vals["width"] = job["width"]
        vals["height"] = job["height"]
    if mode == "fl2v":
        kfs = spec.get("keyframes") or []
        first = next((k["path"] for k in kfs if int(k.get("index", 0)) == 0), None)
        last = next((k["path"] for k in kfs if int(k.get("index", 0)) == -1), None)
        if not first:
            raise ValueError("fl2v 需要 index=0 的首帧")
        vals.update(first=first, last=last,
                    width=job["width"], height=job["height"])
    if mode == "ref2v":
        refs = spec.get("references") or []
        if not refs:
            raise ValueError("ref2v 至少需要一张参考图")
        if len(refs) > tpl.get("max_refs", 2):
            raise ValueError(f"ref2v 模板当前最多 {tpl['max_refs']} 张参考图")
        vals["ref0"] = refs[0]["path"]
        if len(refs) > 1:
            vals["ref1"] = refs[1]["path"]
        # omni/pika 模板把时长/分辨率接到参考节点(与 t2v 同法)
        vals["length"] = frames_for(seconds)
        vals["width"] = job["width"]
        vals["height"] = job["height"]
    vals["prefix"] = f"H3/{job['_outdir']}"
    inject(tpl, wf, **vals)

    st, resp = comfy_json("/prompt", {"prompt": wf, "client_id": job["id"]}, timeout=30)
    if st != 200 or not isinstance(resp, dict) or not resp.get("prompt_id"):
        err = (json.dumps(resp, ensure_ascii=False)[:600] if resp else f"HTTP {st}")
        raise RuntimeError(f"ComfyUI 提交失败: {err}")
    job["prompt_id"] = resp["prompt_id"]
    JOBS[job["id"]] = job
    with ST_LOCK:
        STATE["jobs"][job["id"]] = job
        _write_session_locked()
    emit("job_queued", job_id=job["id"], mode=mode, prompt=prompt[:80])
    return job


def _extract_outputs(entry):
    outs = []
    for node_out in (entry or {}).get("outputs", {}).values():
        if not isinstance(node_out, dict):
            continue
        for item in node_out.values():
            if not isinstance(item, list):
                continue
            for f in item:
                if isinstance(f, dict) and f.get("filename"):
                    outs.append({"filename": f["filename"],
                                 "subfolder": f.get("subfolder", ""),
                                 "type": f.get("type", "output")})
    return outs


def _poll_loop():
    """轮询 ComfyUI /history → job 状态机（3s 间隔；agent 主契约由 /status 提供）。"""
    est = LAST_CLIP_SEC[0] or 170.0
    while not POLL_STOP.is_set():
        POLL_STOP.wait(3)
        for job in list(JOBS.values()):
            if job["state"] not in ("queued", "running") or not job["prompt_id"]:
                continue
            st, hist = comfy_json(f"/history/{job['prompt_id']}", timeout=10)
            entry = (hist or {}).get(job["prompt_id"])
            if entry:
                status_str = (entry.get("status") or {}).get("status_str")
                if status_str == "error":
                    job["state"] = "failed"
                    job["finished_at"] = now_iso()
                    msgs = (entry.get("status") or {}).get("messages", [])[-3:]
                    job["error"] = json.dumps(msgs, ensure_ascii=False)[:800]
                    emit("job_failed", job_id=job["id"], error=job["error"])
                    log(f"[job {job['id']}] FAILED")
                elif entry.get("outputs"):
                    outs = _extract_outputs(entry)
                    if outs:
                        job["state"] = "done"
                        job["finished_at"] = now_iso()
                        dt = max(time.time() - _parse_ts(job["started_at"]), 0.1) if job["started_at"] else None
                        job["elapsed_s"] = round(dt, 1) if dt else None
                        if dt:
                            LAST_CLIP_SEC[0] = dt
                        job["outputs"] = outs
                        job["progress"] = {"pct_est": 100.0, "done": True}
                        emit("job_done", job_id=job["id"], elapsed_s=job["elapsed_s"],
                             outputs=[o["filename"] for o in outs])
                        log(f"[job {job['id']}] DONE {job['elapsed_s']}s")
                continue
            # 尚在队列/执行：排队→运行
            if job["state"] == "queued":
                job["state"] = "running"
                job["started_at"] = now_iso()
                emit("job_started", job_id=job["id"])
            else:
                el = max(time.time() - _parse_ts(job["started_at"]), 0) if job["started_at"] else 0
                pct = min(95.0, el / est * 100) if est else 0
                job["progress"] = {"elapsed_s": round(el, 1), "pct_est": round(pct, 1),
                                   "basis": "measured" if LAST_CLIP_SEC[0] else "guess"}
                emit("job_progress", job_id=job["id"], **job["progress"])
        with ST_LOCK:
            _write_session_locked()


def _parse_ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return time.time()


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _token_ok(self):
        want = CFG.get("auth_token") or ""
        return (not want) or self.headers.get("X-Token", "") == want

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, msg):
        self._send(code, {"ok": False, "error": msg})

    def _qs(self):
        return parse_qs(urlparse(self.path).query)

    def _snapshot(self):
        with ST_LOCK:
            st = {k: (dict(v) if k == "jobs" else v) for k, v in STATE.items()}
        st["machine"] = gpu_info()
        st["comfy"] = {}
        qst, q = comfy_json("/queue", timeout=5)
        if qst == 200 and q:
            st["comfy"] = {"queue_running": len(q.get("queue_running", [])),
                           "queue_pending": len(q.get("queue_pending", []))}
        with EV_LOCK:
            st["events_last_id"] = EVENTS[-1]["id"] if EVENTS else 0
        return st

    def do_GET(self):
        if not self._token_ok():
            return self._err(401, "bad token")
        path, qs = urlparse(self.path).path, self._qs()
        if path == "/health":
            cst, _ = comfy_json("/system_stats", timeout=5)
            ok = cst == 200
            phase = STATE["phase"] if ok else "degraded"
            if ok and STATE["phase"] != "ready":
                set_phase("ready", "ComfyUI 可达, 接受 /generate")
            return self._send(200 if ok else 503,
                              {"ok": ok, "phase": phase, "ts": now_iso()})
        if path == "/state":
            return self._send(200, self._snapshot())
        if path == "/status":
            jid = (qs.get("job_id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self._err(404, f"job not found: {jid}")
            return self._send(200, {k: v for k, v in job.items() if not k.startswith("_")})
        if path == "/result":
            jid = (qs.get("job_id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self._err(404, f"job not found: {jid}")
            files = [{"name": o["filename"], "subfolder": o["subfolder"],
                      "bytes": self._view_size(o)} for o in job["outputs"]]
            return self._send(200, {"job_id": jid, "state": job["state"], "files": files})
        if path == "/files":
            o = {"filename": (qs.get("name") or [""])[0],
                 "subfolder": (qs.get("subfolder") or [""])[0],
                 "type": (qs.get("type") or ["output"])[0]}
            if not o["filename"] or ".." in o["filename"]:
                return self._err(404, "bad name")
            q = urllib.parse.urlencode({k: v for k, v in o.items() if v})
            st, data = comfy_req(f"/view?{q}", timeout=300)
            if st != 200:
                return self._err(404, f"view failed: {st}")
            ctype = "video/mp4" if o["filename"].endswith(".mp4") else "application/octet-stream"
            return self._send(200, data, ctype=ctype)
        if path == "/events":
            return self._sse(int((qs.get("since") or ["0"])[0]))
        return self._err(404, f"unknown path {path}")

    def _view_size(self, o):
        q = urllib.parse.urlencode({k: v for k, v in o.items() if v})
        try:
            req = urllib.request.Request(f"{COMFY}/view?{q}", method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as r:
                return int(r.headers.get("Content-Length") or 0)
        except Exception:
            return 0

    def _sse(self, since):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        last = since
        try:
            while True:
                with EV_LOCK:
                    pending = [e for e in EVENTS if e["id"] > last]
                    if pending:
                        last = pending[-1]["id"]
                    elif not EV_COND.wait(timeout=15):
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                for ev in pending:
                    self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
        except (ConnectionError, BrokenPipeError):
            pass

    def do_POST(self):
        if not self._token_ok():
            return self._err(401, "bad token")
        path, qs = urlparse(self.path).path, self._qs()
        if path == "/generate":
            with ST_LOCK:
                ph = STATE["phase"]
            if ph not in ("ready", "degraded"):
                return self._err(409, f"server not ready (phase={ph})")
            if ph == "degraded":
                return self._err(409, "ComfyUI 不可达（degraded）, 稍后重试或查 /state")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                spec = json.loads(self.rfile.read(length) or b"{}")
                job = submit_job(spec)
            except (ValueError, json.JSONDecodeError) as e:
                return self._err(400, str(e))
            except RuntimeError as e:
                return self._err(502, str(e))
            return self._send(202, {"ok": True, "job_id": job["id"], "state": job["state"]})
        if path == "/cancel":
            jid = (qs.get("job_id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self._err(404, f"job not found: {jid}")
            job["cancel_requested"] = True
            acted = "flagged"
            if job["state"] == "running" and job["prompt_id"]:
                comfy_json("/interrupt", {}, timeout=10)
                acted = "interrupted (running)"
            elif job["state"] == "queued" and job["prompt_id"]:
                comfy_json("/queue", {"delete": [job["prompt_id"]]}, timeout=10)
                job["state"] = "cancelled"
                job["finished_at"] = now_iso()
                acted = "deleted (queued)"
            emit("job_cancel", job_id=jid, acted=acted)
            return self._send(200, {"ok": True, "acted": acted})
        if path == "/upload":
            name = os.path.basename((qs.get("name") or [""])[0])
            if not name:
                return self._err(400, "需要 ?name=<文件名>")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 200 << 20:
                return self._err(400, f"body 大小异常: {length}")
            os.makedirs(CUI_INPUT, exist_ok=True)
            dst = os.path.join(CUI_INPUT, re.sub(r"[^\w\-.]+", "_", name))
            body = self.rfile.read(length)  # 按 Content-Length 精确读（keep-alive 无 EOF）
            with open(dst, "wb") as f:
                f.write(body)
            return self._send(200, {"ok": True, "path": os.path.basename(dst),
                                    "bytes": os.path.getsize(dst)})
        return self._err(404, f"unknown path {path}")


# ---------------------------------------------------------------- main

def main():
    STATE["session"] = {"started_at": now_iso()}
    with ST_LOCK:
        STATE["machine"] = gpu_info()
        STATE["weights"] = _weights_info()
        _write_session_locked()
    log(f"h3api starting: root={ROOT} port={PORT} comfy={COMFY} dryrun={DRYRUN}")

    def _wait_comfy():
        for i in range(7200):  # 守护循环负责拉起 ComfyUI; 外壳只等待+汇报
            st, _ = comfy_json("/system_stats", timeout=5)
            if st == 200:
                set_phase("ready", "ComfyUI 可达")
                return
            if STATE["phase"] != "degraded":
                set_phase("degraded", f"ComfyUI 未就绪(等待守护拉起, {i*15}s)")
            POLL_STOP.wait(15)

    threading.Thread(target=_wait_comfy, daemon=True).start()
    threading.Thread(target=_poll_loop, daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    log(f"listening on 127.0.0.1:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
