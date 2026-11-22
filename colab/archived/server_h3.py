#!/usr/bin/env python3
"""H3 常驻推理 server（v0.3 agent 闭环）— 纯 stdlib, 零第三方启动依赖。

启动归属反转（docs/plan-nocomfy.md）: 本进程从第 0 秒起就监听并提供 /state /events,
启动编排全部收编进本进程（子进程跑 deps → weights → load → READY）,
人类面板/agent 从提交那一刻起就有阶段进度流, 不再黑盒等待。

消费方优先级: **AI agent 为主**（轮询 /state /status 是主契约, fire-and-poll）,
SSE 仅服务人类 UI 实时视图。

API 契约（绑定 127.0.0.1, 经 SSH 隧道访问）:
  GET  /health                  200 {"ok":true,"phase":..} / 503（守护探活）
  GET  /state                   全量快照: 阶段/时间戳/权重/GPU/job 队列与历史
  GET  /status?job_id=..        单 job 状态（agent 主轮询点）
  GET  /result?job_id=..        产出文件列表
  GET  /files?job_id=..&name=.. 产出文件字节（经隧道下载）
  GET  /events?since=N          SSE 事件流（job 进度节流 ≤1s）
  POST /generate {...}          提交生成, 202 {"job_id":..}; 单卡 FIFO 排队
  POST /cancel?job_id=..        queued 直接取消; running 仅置 cancel_requested
  POST /upload?name=..          上传首尾帧/参考图（body=原始字节）→ {"path":VM路径}

/generate 请求体:
  {"mode":"t2v|fl2v|ref2v", "prompt":"...", "seed":42, "seconds":5,
   "width":832, "height":480, "name":"demo",
   "keyframes":[{"path":"/content/h3/uploads/x.jpg","index":0}],   # fl2v: 0=首帧 -1=尾帧
   "references":[{"type":"image|video|audio","path":"..."}]}        # ref2v

模型加载策略（config.json load_strategy, D1 第一验证项 = int8_local）:
  int8_local  复用 Comfy 重打包 INT8 convrot 四件套（本盘已下, plan 权重策略第一期）
  int8_hf     同四件, 但由 DiffSynth 直接从 HF 仓库拉（不依赖本盘布局）
  nf4_hf      DiffSynth 官方 NF4 包（base 50 步, 仅兜底）

H3_DRYRUN=1 时为冒烟模式: 跳过 pip/权重/模型加载, 用桩管道走通全部 API（本地开发用）。
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.environ.get("H3_ROOT", "/content/h3")
DRYRUN = os.environ.get("H3_DRYRUN") == "1"
os.makedirs(ROOT, exist_ok=True)
SERVER_LOG = f"{ROOT}/server.log"
SESSION_JSON = f"{ROOT}/session.json"
SETUP_DONE = f"{ROOT}/.setup_done"
UPLOADS = f"{ROOT}/uploads"
OUTPUTS = f"{ROOT}/outputs"

CFG = {"port": 8188, "expose": "ssh", "auth_token": "", "load_strategy": "int8_local",
       "steps": 8, "fps": 24, "width": 832, "height": 480, "seconds": 5,
       "shift_video": 12.0, "shift_audio": 3.0}
try:
    with open(f"{ROOT}/config.json") as f:
        CFG.update(json.load(f))
except Exception:
    pass
PORT = int(CFG["port"])

# ---------------------------------------------------------------- event bus / state

EVENTS = []          # [{id, ts, type, ...}]
EV_LOCK = threading.Lock()
EV_COND = threading.Condition(EV_LOCK)
MAX_EVENTS = 2000

STATE = {
    "phase": "booting",          # booting→deps→weights→loading→ready | failed
    "phase_detail": "",
    "error": None,
    "runtime": "server",
    "machine": {},
    "weights": {"done": [], "bytes": 0},
    "config": {"load_strategy": CFG["load_strategy"], "steps": CFG["steps"]},
    "jobs": {},                  # job_id → record（含历史）
    "session": {},               # 时间戳: started_at/deps_done_at/weights_done_at/ready_at
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


def set_phase(phase, detail=""):
    with ST_LOCK:
        STATE["phase"] = phase
        STATE["phase_detail"] = detail
        key = {"deps": None, "weights": None}.get(phase)
        if phase == "ready":
            STATE["session"]["ready_at"] = now_iso()
        elif phase == "failed":
            STATE["session"]["failed_at"] = now_iso()
        _write_session_locked()
    emit("phase", phase=phase, detail=detail)


def _write_session_locked():
    with open(SESSION_JSON, "w") as f:
        json.dump({"session": STATE["session"], "phase": STATE["phase"],
                   "weights": STATE["weights"], "jobs": STATE["jobs"]},
                  f, ensure_ascii=False, indent=1)


def sh_quiet(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()


def gpu_info():
    out = sh_quiet("nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader")
    return {"raw": out or None}


def sh(cmd, check=False):
    """子进程执行, 输出行进事件流（进度可见）。"""
    log(f"$ {cmd}")
    emit("log", line=f"$ {cmd}")
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(f"  {line[:300]}")
            emit("log", line=line[:300])
    p.wait()
    if p.returncode != 0 and check:
        raise RuntimeError(f"command failed ({p.returncode}): {cmd}")
    return p.returncode


# ---------------------------------------------------------------- boot stages

def stage_deps():
    set_phase("deps", "pip install diffsynth")
    if DRYRUN:
        time.sleep(0.3)
        log("[deps] DRYRUN skip")
    else:
        pip = f"{sys.executable} -m pip install -q"
        sh(f"{pip} -U huggingface_hub || true")
        # quant extra = comfy-kitchen 后端: 加载 Comfy 重打包 INT8 convrot 权重必需
        rc = sh(f'{pip} "diffsynth[quant]"')
        if rc != 0:
            log("[deps] diffsynth[quant] 失败, 降级装 diffsynth（INT8 可能不可用, D1 现场判断）")
            sh(f"{pip} diffsynth", check=True)
    with ST_LOCK:
        STATE["session"]["deps_done_at"] = now_iso()
    log("[deps] OK")


def stage_weights():
    set_phase("weights", "HF 优先 / GCS 兜底（复用 bootstrap 下载逻辑）")
    if DRYRUN:
        time.sleep(0.3)
        log("[weights] DRYRUN skip")
        with ST_LOCK:
            STATE["session"]["weights_done_at"] = now_iso()
            STATE["weights"] = {"done": ["(dryrun)"], "bytes": 0}
        return
    sys.path.insert(0, ROOT)
    import bootstrap_h3 as boot
    _orig_log = boot.log

    def _emitting_log(msg):  # bootstrap 的下载进度行 → 同时进事件流（面板/agent 可见）
        _orig_log(msg)
        emit("log", line=msg)

    boot.log = _emitting_log
    t0 = time.time()
    boot.stage_weights()          # 幂等: .done 存在即秒回; HF 失败自动降级 GCS
    boot.log = _orig_log
    files, total = [], 0
    for base, _dirs, names in os.walk(boot.LOCAL_MODELS):
        for n in names:
            p = os.path.join(base, n)
            if os.path.isfile(p):
                files.append(os.path.relpath(p, boot.LOCAL_MODELS))
                total += os.path.getsize(p)
    with ST_LOCK:
        STATE["session"]["weights_done_at"] = now_iso()
        STATE["weights"] = {"done": files, "bytes": total}
    log(f"[weights] OK ({total/1e9:.1f} GB, {time.time()-t0:.0f}s)")


# ---- 模型加载（D1 第一验证项: Comfy INT8 convrot 四件套能否进 DiffSynth） ----

ROLE_FILES = {
    "dit": "diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors",
    "text_encoder": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "video_vae": "vae/minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "vae/minimax_h3_audio_vae_fp32.safetensors",
}


def _resolve_local(rel):
    """本盘权重可能在 LOCAL_MODELS 根或按角色子目录（HF 快照保留仓库层级）。"""
    import bootstrap_h3 as boot
    for cand in (os.path.join(boot.LOCAL_MODELS, rel),
                 os.path.join(boot.LOCAL_MODELS, os.path.basename(rel))):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(f"权重缺失: {rel} (LOCAL_MODELS={boot.LOCAL_MODELS})")


def call_tolerant(fn, label, **kwargs):
    """逐步丢弃 fn 不支持的 kwargs（仅认 TypeError 的 unexpected keyword）,
    DiffSynth 具体 API 细节由 D1 现场校准——丢弃项都会进事件流。"""
    for _ in range(6):
        try:
            return fn(**kwargs)
        except TypeError as e:
            m = re.search(r"unexpected keyword argument '(\w+)'", str(e))
            if not m or m.group(1) not in kwargs:
                raise
            dropped = m.group(1)
            emit("warn", where=label, detail=f"drop unsupported kwarg: {dropped}")
            log(f"[{label}] drop unsupported kwarg: {dropped}")
            kwargs.pop(dropped)
    raise RuntimeError(f"{label}: too many unsupported kwargs")


def _dit_path():
    """DiT 路径可被 config.json 的 dit_override 覆盖。

    实测结论（D1，2026-11-22）: FastVideo fasth3_8step_v2 蒸馏权重每块多
    `attn.to_gate_compress`，DiffSynth(2.2.3) 无该模块 → 无法加载；
    底模走 Comfy-Org 重打包 pruned INT8（DiffSynth 原生注册 hash），50 步。
    蒸馏加速恢复属后续专项（移植 ComfyUI gate_compress 或验证 turbo LoRA）。"""
    p = CFG.get("dit_override")
    if p and os.path.exists(p):
        return p
    return _resolve_local(ROLE_FILES["dit"])


def _load_int8_local(vram_limit_gb):
    """本盘 INT8 convrot 四件套 → ModelConfig(path=[..])。API 细节不匹配时错误全文进事件流。"""
    import torch
    from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig
    vram_config = {
        "offload_dtype": torch.bfloat16, "offload_device": "cpu",
        "onload_dtype": torch.bfloat16, "onload_device": "cpu",
        "preparing_dtype": torch.bfloat16, "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16, "computation_device": "cuda",
    }
    mcs = []
    for role, rel in ROLE_FILES.items():
        path = _dit_path() if role == "dit" else _resolve_local(rel)
        emit("log", line=f"model_config[{role}] = {path}")
        try:
            mcs.append(ModelConfig(path=[path], **vram_config))
        except TypeError as e:
            emit("warn", where="int8_local", detail=f"ModelConfig(path=..) 不支持: {e}")
            mcs.append(ModelConfig(path=[path]))
    processor = ModelConfig(model_id="MiniMax/MiniMax-H3", origin_file_pattern="FL2VA/processor/")
    pipe = call_tolerant(
        MiniMaxH3Pipeline.from_pretrained, "from_pretrained",
        torch_dtype=torch.bfloat16, device="cuda",
        model_configs=mcs, processor_config=processor, vram_limit=vram_limit_gb)
    return pipe


def _load_int8_hf(vram_limit_gb):
    """同四件但由 DiffSynth 直接从 HF 仓库拉（权重仍走 HF 直下, 不依赖本盘布局）。"""
    import torch
    from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig
    repo = "FastVideo/FastVideo-FastH3-Comfy"
    vram_config = {
        "offload_dtype": torch.bfloat16, "offload_device": "cpu",
        "onload_dtype": torch.bfloat16, "onload_device": "cpu",
        "preparing_dtype": torch.bfloat16, "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16, "computation_device": "cuda",
    }
    mcs = [ModelConfig(model_id=repo, origin_file_pattern=rel, **vram_config)
           for rel in ROLE_FILES.values()]
    processor = ModelConfig(model_id="MiniMax/MiniMax-H3", origin_file_pattern="FL2VA/processor/")
    return call_tolerant(
        MiniMaxH3Pipeline.from_pretrained, "from_pretrained",
        torch_dtype=torch.bfloat16, device="cuda",
        model_configs=mcs, processor_config=processor, vram_limit=vram_limit_gb)


def _load_nf4(vram_limit_gb):
    """官方 NF4 包（base 50 步, plan 备选 B, 仅兜底——慢 ~6×）。"""
    import torch
    from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig
    vram_config = {
        "offload_dtype": torch.bfloat16, "offload_device": "cpu",
        "onload_dtype": torch.bfloat16, "onload_device": "cpu",
        "preparing_dtype": torch.bfloat16, "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16, "computation_device": "cuda",
    }
    mid = "DiffSynth-Studio/MiniMax-H3-NF4"
    mcs = [ModelConfig(model_id=mid, origin_file_pattern=p, **vram_config) for p in (
        "minimax-h3-fl2va-nf4.safetensors", "minimax-h3-text-encoder-nf4.safetensors",
        "video_vae_nf4.safetensors", "audio_vae_nf4.safetensors")]
    processor = ModelConfig(model_id="MiniMax/MiniMax-H3", origin_file_pattern="FL2VA/processor/")
    return call_tolerant(
        MiniMaxH3Pipeline.from_pretrained, "from_pretrained",
        torch_dtype=torch.bfloat16, device="cuda",
        model_configs=mcs, processor_config=processor, vram_limit=vram_limit_gb)


def stage_load():
    set_phase("loading", f"DiffSynth MiniMaxH3Pipeline (strategy={CFG['load_strategy']})")
    if DRYRUN:
        time.sleep(0.5)
        PIPE = _DryPipe()
    else:
        import torch
        info = torch.cuda.mem_get_info("cuda")
        vram_gb = info[1] / 1024 ** 3 - 2
        emit("log", line=f"vram_limit = {vram_gb:.1f} GB")
        strategies = {"int8_local": _load_int8_local, "int8_hf": _load_int8_hf, "nf4_hf": _load_nf4}
        fn = strategies.get(CFG["load_strategy"])
        if not fn:
            raise RuntimeError(f"未知 load_strategy: {CFG['load_strategy']}")
        try:
            PIPE = fn(vram_gb)
        except Exception as e:  # noqa: BLE001
            emit("error", where=f"load[{CFG['load_strategy']}]",
                 detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}")
            raise
    globals()["PIPE"] = PIPE
    open(SETUP_DONE, "w").write(now_iso())
    set_phase("ready", "模型已进显存, 接受 /generate")


class _DryPipe:
    """冒烟桩: 走通 job 全流程, 产出假文件（本地开发用, 不进 GPU 路径）。"""

    def __call__(self, **kwargs):
        time.sleep(2)
        return [b"FAKE"], None


# ---------------------------------------------------------------- job queue

QUEUE = deque()
QUEUE_LOCK = threading.Lock()
JOBS = {}          # job_id → record
JOB_SEQ = [0]
CANCEL_FLAGS = {}  # job_id → bool（running 态仅标记, D1 无步级回调不强杀）
LAST_CLIP_SEC = [None]  # 上一条实测耗时 → 进度估算基线（风险清单 #6 的估算回退）


def new_job_id():
    with QUEUE_LOCK:
        JOB_SEQ[0] += 1
        return f"j{JOB_SEQ[0]:04d}-{time.strftime('%H%M%S')}"


def submit_job(spec):
    mode = spec.get("mode", "t2v")
    if mode not in ("t2v", "fl2v", "ref2v"):
        raise ValueError(f"mode 须为 t2v|fl2v|ref2v, 收到 {mode!r}")
    prompt = (spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    seconds = float(spec.get("seconds", CFG["seconds"]))
    if not 4 <= seconds <= 15:
        raise ValueError(f"seconds 超出模型范围 4~15: {seconds}")
    job = {
        "id": new_job_id(),
        "mode": mode,
        "prompt": prompt,
        "seed": int(spec.get("seed", 42)),
        "seconds": seconds,
        "width": int(spec.get("width", CFG["width"])),
        "height": int(spec.get("height", CFG["height"])),
        "num_frames": (round(seconds * CFG["fps"]) // 4) * 4 + 4,
        "name": (spec.get("name") or "").strip() or None,
        "keyframes": spec.get("keyframes") or [],
        "references": spec.get("references") or [],
        "state": "queued",
        "submitted_at": now_iso(),
        "started_at": None, "finished_at": None,
        "elapsed_s": None, "progress": None,
        "error": None, "outputs": [],
        "cancel_requested": False,
    }
    with QUEUE_LOCK:
        JOBS[job["id"]] = job
        QUEUE.append(job["id"])
    with ST_LOCK:
        STATE["jobs"][job["id"]] = job
        _write_session_locked()
    emit("job_queued", job_id=job["id"], mode=mode, prompt=prompt[:80])
    return job


def _safe_outdir(job):
    name = job["name"] or job["id"]
    name = re.sub(r"[^\w\-.]+", "_", name)  # 防路径注入
    d = os.path.join(OUTPUTS, name)
    os.makedirs(d, exist_ok=True)
    return d


def _job_kwargs(job):
    kw = {"prompt": job["prompt"], "seed": job["seed"],
          "width": job["width"], "height": job["height"],
          "num_frames": job["num_frames"], "num_inference_steps": int(CFG["steps"]),
          "cfg_scale": 1.0,  # CFG 蒸馏: 文档明确 negative/cfg 无效, 固定 1.0
          }
    if job["mode"] == "fl2v":
        kw["keyframes"] = [k["path"] for k in job["keyframes"]]
        kw["keyframe_indices"] = [int(k.get("index", 0)) for k in job["keyframes"]]
    elif job["mode"] == "ref2v":
        kw["references"] = [{"type": r.get("type", "image"), "path": r["path"]}
                            for r in job["references"]]
    # 蒸馏对齐: Comfy 工作流的 MiniMaxH3SigmaShift(shift_video=12, shift_audio=3),
    # DiffSynth 侧参数名未知 → call_tolerant 会自动丢弃不支持项（事件流可见）
    kw["shift_video"] = float(CFG["shift_video"])
    kw["shift_audio"] = float(CFG["shift_audio"])
    return kw


def _run_job(job):
    job["state"] = "running"
    job["started_at"] = now_iso()
    t0 = time.time()
    emit("job_started", job_id=job["id"], mode=job["mode"], seed=job["seed"],
         num_frames=job["num_frames"])
    est = LAST_CLIP_SEC[0] or 170.0  # 8-step 实测 ~2.9min; 首条以它为粗估
    outdir = _safe_outdir(job)
    out_path = os.path.join(outdir, f"{job['id']}.mp4")
    stop = threading.Event()

    def _progress():
        while not stop.wait(5):
            el = time.time() - t0
            pct = min(95.0, el / est * 100)
            job["progress"] = {"elapsed_s": round(el, 1), "est_total_s": round(est, 1),
                               "pct_est": round(pct, 1), "basis": "measured" if LAST_CLIP_SEC[0] else "guess"}
            emit("job_progress", job_id=job["id"], **job["progress"])

    threading.Thread(target=_progress, daemon=True).start()
    try:
        if DRYRUN:
            video, audio = PIPE(prompt=job["prompt"])
            with open(out_path, "wb") as f:
                f.write(video[0] if isinstance(video, list) else (video or b"FAKE"))
        else:
            from diffsynth.utils.data.audio_video import write_video_audio
            video, audio = call_tolerant(PIPE, "pipe", **_job_kwargs(job))
            write_video_audio(video=video, audio=audio, output_path=out_path,
                              fps=int(CFG["fps"]), audio_sample_rate=32000)
        dt = time.time() - t0
        LAST_CLIP_SEC[0] = dt
        job["elapsed_s"] = round(dt, 1)
        job["finished_at"] = now_iso()
        job["outputs"] = [os.path.basename(out_path)]
        job["progress"] = {"elapsed_s": job["elapsed_s"], "pct_est": 100.0, "done": True}
        job["state"] = "done"
        emit("job_done", job_id=job["id"], elapsed_s=job["elapsed_s"],
             outputs=job["outputs"], outdir=outdir)
        log(f"[job {job['id']}] DONE {job['elapsed_s']}s → {out_path}")
    except Exception as e:  # noqa: BLE001
        job["state"] = "failed"
        job["finished_at"] = now_iso()
        job["elapsed_s"] = round(time.time() - t0, 1)
        job["error"] = f"{type(e).__name__}: {e}"
        emit("job_failed", job_id=job["id"], error=job["error"],
             detail=traceback.format_exc()[-1500:])
        log(f"[job {job['id']}] FAILED: {job['error']}")
    finally:
        stop.set()
        with ST_LOCK:
            _write_session_locked()


def _worker():
    while True:
        with QUEUE_LOCK:
            jid = QUEUE.popleft() if QUEUE else None
        if jid is None:
            time.sleep(0.5)
            continue
        job = JOBS.get(jid)
        if not job or job["state"] != "queued":
            continue
        _run_job(job)


def _fmt_event(ev):
    return f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认访问日志（server.log 只记要紧事）
        pass

    # ---- helpers
    def _token_ok(self):
        want = CFG.get("auth_token") or ""
        if not want:
            return True
        return self.headers.get("X-Token", "") == want

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

    def _state_snapshot(self):
        with ST_LOCK:
            st = dict(STATE)
            st["jobs"] = {jid: dict(j) for jid, j in STATE["jobs"].items()}
        st["machine"] = gpu_info()
        with EV_LOCK:
            st["events_last_id"] = EVENTS[-1]["id"] if EVENTS else 0
        return st

    def _out_file(self, jid, name):
        """产出文件定位（防穿越: 只允许 outputs/<name 去特殊字符>/ 下、basename 无 ..）。"""
        job = JOBS.get(jid)
        if not job:
            return None
        if not name or "/" in name or ".." in name or name.startswith("."):
            return None
        d = _safe_outdir(job)
        p = os.path.join(d, os.path.basename(name))
        return p if os.path.isfile(p) else None

    # ---- GET
    def do_GET(self):
        if not self._token_ok():
            return self._err(401, "bad token")
        path, qs = urlparse(self.path).path, self._qs()
        if path == "/health":
            with ST_LOCK:
                ph = STATE["phase"]
            body = {"ok": ph != "failed", "phase": ph, "ts": now_iso()}
            return self._send(200 if body["ok"] else 503, body)
        if path == "/state":
            return self._send(200, self._state_snapshot())
        if path == "/status":
            jid = (qs.get("job_id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self._err(404, f"job not found: {jid}")
            return self._send(200, dict(job))
        if path == "/result":
            jid = (qs.get("job_id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self._err(404, f"job not found: {jid}")
            outdir = _safe_outdir(job)
            files = [{"name": n, "bytes": os.path.getsize(os.path.join(outdir, n))}
                     for n in sorted(os.listdir(outdir))]
            return self._send(200, {"job_id": jid, "state": job["state"],
                                    "outdir": outdir, "files": files})
        if path == "/files":
            jid = (qs.get("job_id") or [""])[0]
            name = (qs.get("name") or [""])[0]
            p = self._out_file(jid, name)
            if not p:
                return self._err(404, f"file not found: job={jid} name={name}")
            with open(p, "rb") as f:
                data = f.read()
            ctype = "video/mp4" if name.endswith(".mp4") else "application/octet-stream"
            return self._send(200, data, ctype=ctype)
        if path == "/events":
            return self._sse(int((qs.get("since") or ["0"])[0]))
        return self._err(404, f"unknown path {path}")

    def _sse(self, since):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")  # 无 Content-Length, 显式短连（stdlib 无 chunked）
        self.end_headers()
        last = since
        try:
            while True:
                with EV_LOCK:
                    pending = [e for e in EVENTS if e["id"] > last]
                    if pending:
                        last = pending[-1]["id"]
                    elif not EV_COND.wait(timeout=15):
                        self.wfile.write(b": ping\n\n")  # 心跳
                        self.wfile.flush()
                        continue
                for ev in pending:
                    self.wfile.write(_fmt_event(ev).encode())
                self.wfile.flush()
        except (ConnectionError, BrokenPipeError):
            pass  # 客户端断开, 正常结束

    # ---- POST
    def do_POST(self):
        if not self._token_ok():
            return self._err(401, "bad token")
        path, qs = urlparse(self.path).path, self._qs()
        if path == "/generate":
            with ST_LOCK:
                ph = STATE["phase"]
            if ph != "ready":
                return self._err(409, f"server not ready (phase={ph}), 拒绝入队; 看 /events 等就绪")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                spec = json.loads(self.rfile.read(length) or b"{}")
                job = submit_job(spec)
            except (ValueError, json.JSONDecodeError) as e:
                return self._err(400, str(e))
            return self._send(202, {"ok": True, "job_id": job["id"], "state": job["state"]})
        if path == "/cancel":
            jid = (qs.get("job_id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self._err(404, f"job not found: {jid}")
            with QUEUE_LOCK:
                if job["state"] == "queued" and jid in QUEUE:
                    QUEUE.remove(jid)
                    job["state"] = "cancelled"
                    job["finished_at"] = now_iso()
                    acted = "cancelled (queued)"
                else:
                    CANCEL_FLAGS[jid] = True
                    job["cancel_requested"] = True
                    acted = f"flagged (state={job['state']}; D1 无步级回调, running 态不强杀)"
            emit("job_cancel", job_id=jid, acted=acted)
            return self._send(200, {"ok": True, "acted": acted})
        if path == "/upload":
            name = (qs.get("name") or [""])[0]
            name = os.path.basename(name)
            if not name:
                return self._err(400, "需要 ?name=<文件名>")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 200 << 20:
                return self._err(400, f"body 大小异常: {length}")
            os.makedirs(UPLOADS, exist_ok=True)
            dst = os.path.join(UPLOADS, re.sub(r"[^\w\-.]+", "_", name))
            body = self.rfile.read(length)  # 按 Content-Length 精确读（keep-alive 下无 EOF 可等）
            with open(dst, "wb") as f:
                f.write(body)
            return self._send(200, {"ok": True, "path": dst, "bytes": os.path.getsize(dst)})
        return self._err(404, f"unknown path {path}")


# ---------------------------------------------------------------- main

def main():
    STATE["session"] = {"started_at": now_iso()}
    with ST_LOCK:
        STATE["machine"] = gpu_info()
        STATE["runtime"] = "server"
    log(f"server_h3 starting: root={ROOT} port={PORT} dryrun={DRYRUN} "
        f"strategy={CFG['load_strategy']}")

    def _boot():
        try:
            stage_deps()
            stage_weights()
            stage_load()
        except Exception as e:  # noqa: BLE001
            with ST_LOCK:
                STATE["phase"] = "failed"
                STATE["error"] = f"{type(e).__name__}: {e}"
                STATE["phase_detail"] = traceback.format_exc()[-800:]
                _write_session_locked()
            emit("error", phase="failed", detail=f"{type(e).__name__}: {e}")
            log(f"BOOT FAILED: {e}")

    threading.Thread(target=_boot, daemon=True).start()
    threading.Thread(target=_worker, daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    log(f"listening on 127.0.0.1:{PORT} (phase=booting, 启动进度看 /state /events)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
