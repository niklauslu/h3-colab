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
   "references":[{"type":"image","path":<input文件名>}]}    # ref2v: 模板现支持 3 张(单/双/三参考自动整形接线)

节点寻址全部封装在 TEMPLATES 注入表里（来自 v0.2 实测工作流）。
H3_DRYRUN=1 时内置假 ComfyUI，本地冒烟全链路（不碰 GPU）。
"""

import json
import math
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
_T_TURBO = {
    # 官方 turbo LoRA 配方 + Pika 变体(抽屉同挂, spec.recipe 选择, 同 VM 一次跑全部对比臂)
    # t2v/i2v/fl2v → fl2va 底模; ref2v → ref2va 底模; fl2v 的 lora_name 可注入切 8步/4步/省略=8步默认
    "t2v": {
        "file": "turbo_t2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("12", "seed"),
                   "steps": ("12", "steps"), "width": ("7", "width"),
                   "height": ("7", "height"), "length": ("7", "length"),
                   "prefix": ("17", "filename_prefix")},
        "length_default": 124,
    },
    "t2v@pika": {
        "file": "pika_t2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("10", "noise_seed"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "length": ("7", "length"), "prefix": ("17", "filename_prefix")},
        "length_default": 124,
    },
    "fl2v": {
        "file": "turbo_fl2v_api.json",
        "inject": {"prompt": ("9", "prompt"), "seed": ("12", "seed"),
                   "steps": ("12", "steps"), "width": ("9", "width"),
                   "height": ("9", "height"), "length": ("9", "length"),
                   "first": ("5", "image"), "last": ("6", "image"),
                   "lora_name": ("10", "lora_name"),
                   "prefix": ("20", "filename_prefix")},
        "length_default": 124,
    },
    "fl2v@pika": {
        "file": "pika_fl2v_api.json",
        "inject": {"prompt": ("9", "prompt"), "seed": ("13", "noise_seed"),
                   "width": ("9", "width"), "height": ("9", "height"),
                   "length": ("9", "length"), "first": ("5", "image"), "last": ("6", "image"),
                   "prefix": ("20", "filename_prefix")},
        "length_default": 124,
    },
    "ref2v": {
        "file": "turbo_ref2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("12", "seed"),
                   "steps": ("12", "steps"), "ref0": ("5", "image"), "ref1": ("6", "image"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "length": ("7", "length"), "prefix": ("17", "filename_prefix")},
        "max_refs": 3,
    },
    "ref2v@pika": {
        "file": "pika_ref2v_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("10", "noise_seed"),
                   "ref0": ("5", "image"), "ref1": ("6", "image"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "length": ("7", "length"), "prefix": ("17", "filename_prefix")},
        "max_refs": 3,
    },
}
_T_IMAGE = {
    # H3 生图配方: 6步 CFG-free 蒸馏, 无负向; 编辑同 checkpoint(参考图经 conditioning)
    "t2i": {
        "file": "image_t2i_api.json",
        "inject": {"prompt": ("5", "prompt"), "seed": ("7", "seed"),
                   "width": ("5", "width"), "height": ("5", "height"),
                   "prefix": ("9", "filename_prefix")},
    },
    "edit": {
        "file": "image_edit_api.json",
        "inject": {"prompt": ("7", "prompt"), "seed": ("9", "seed"),
                   "ref0": ("4", "image"),
                   "prefix": ("11", "filename_prefix")},
        "max_refs": 1,
    },
}
_T_TAO = {
    # TaoMate 3步实验档: 同模板 A/B —— lora_strength=0+8步=基线, 1.0+3步=TaoMate
    # (lora_strength 由 spec 传入; 默认 1.0)
    "fl2v": {
        "file": "fl2v_tao_api.json",
        "inject": {"prompt": ("9", "prompt"), "seed": ("12", "seed"),
                   "steps": ("12", "steps"), "width": ("9", "width"),
                   "height": ("9", "height"), "length": ("9", "length"),
                   "first": ("5", "image"), "last": ("6", "image"),
                   "lora_strength": ("17", "strength_model"),
                   "prefix": ("16", "filename_prefix")},
        "length_default": 124,
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
        "max_refs": 3,
    },
}
_T_ZIMAGE = {
    # Z-Image-Turbo(2026-11-22 复活, 官方教程口径): 6B DiT, 8步 cfg1 res_multistep
    # + AuraFlow shift3; 负向=ConditioningZeroOut(cfg1 无效占位); T4(16G) 官方可跑
    "t2i": {
        "file": "zimage_t2i_api.json",
        "inject": {"prompt": ("5", "text"), "seed": ("12", "seed"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "prefix": ("9", "filename_prefix")},
    },
}
_T_QWEN21T = {
    # Qwen-Image-2.1-Turbo(2026-11-22 复活, 官方教程口径): 20B int8, 8步 euler
    # + ManualSigmas(官方9值勿动) + cfg1; steps 烘焙在调度里不可注入(inject 自动跳过)
    # t2i 尺寸任意(原生2K, 8px 对齐); 编辑 latent 跟随 image_1 原尺寸(resolution=像素预算)
    "t2i": {
        "file": "qwen21t_t2i_api.json",
        "inject": {"prompt": ("5", "prompt"), "seed": ("11", "noise_seed"),
                   "width": ("7", "width"), "height": ("7", "height"),
                   "prefix": ("9", "filename_prefix")},
    },
    "edit": {
        "file": "qwen21t_edit_api.json",
        "inject": {"prompt": ("5", "prompt"), "seed": ("11", "noise_seed"),
                   "ref0": ("4", "image"), "ref1": ("6", "image"),
                   "prefix": ("9", "filename_prefix")},
        # 官方语义: image_1=画布(输出构图跟随它), image_2+=材料(提示词 <image2> 引用)
        # ref1 经独立 LoadImage("6") 转张量再入槽——直接塞路径字符串会炸 movedim
        "max_refs": 2,  # 节点有 16 槽, 契约先放开 2(画布+1材料)
        "optional": {"ref1": (["6"], [("5", "images.image_2")])},
    },
}
TEMPLATES_BY_PROFILE = {
    "h3pika": _T_PIKA,    # ref2v 主力(实测 112s)
    "h3turbo": _T_TURBO,  # 官方 turbo 配方(t2v 8步 / ref2v 4步)
    "h3image": _T_IMAGE,  # 生图(6步 CFG-free)
    "h3omni": _T_OMNI,    # 全能单 DiT(后续主力扩展位)
    "h3tao": _T_TAO,      # TaoMate 实验(挂起)
    "zimage": _T_ZIMAGE,      # 生图便宜档(T4/L4)
    "qwen21t": _T_QWEN21T,    # 生图编辑档(L4; T4 触发流送会慢)
    # h3core=纯底座无生成配方
}
TEMPLATES = _T_PIKA  # 兜底(未知档案) = ref2v 主力配方


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
        raise ValueError(f"档案 {CFG.get('weights_profile', 'h3pika')} 的 mode 须为 "
                         f"{list(_profile_templates())}, 收到 {mode!r}")
    path = os.path.join(WF_DIR, t["file"])
    if not os.path.exists(path):
        raise RuntimeError(f"模板缺失: {path}（bootstrap 应上传 workflows/）")
    with open(path) as f:
        return t, json.load(f)


def inject(tpl, wf, **vals):
    str_keys = set(tpl.get("str_keys") or [])
    for key, val in vals.items():
        # 模板未暴露的参数直接忽略(如 omni/pika 固定步数的 steps)——
        # 2026-11-22 实测: KeyError 会在外壳处理线程崩成 EOF, 必须容错
        if val is None or key not in tpl["inject"]:
            continue
        node, input_key = tpl["inject"][key]
        if node not in wf:
            raise RuntimeError(f"模板缺节点 {node}")
        if key in str_keys:  # 坑㉘: PDD nfe 等节点要字符串枚举
            val = str(val)
        wf[node]["inputs"][input_key] = val


def frames_for(seconds):
    # 官方帧格点 17k+5(向上取整): 5s→124, 15s→362; 73/90/107/124≈3.04/3.75/4.46/5.17s
    # (MiniMax 官方口径 "Durations are rounded up to the supported frame grid")
    frames = round(float(seconds) * CFG["fps"])
    k = max(math.ceil((frames - 5) / 17), 4)
    return min(k * 17 + 5, 362)


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
    recipe = (spec.get("recipe") or "").strip()
    key = f"{mode}@{recipe}" if recipe else mode
    tbl = _profile_templates()
    if key not in tbl:
        raise ValueError(f"配方不存在: {key!r} (可用: {list(tbl)})")
    tpl, wf = load_template(key)
    prompt = (spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    seconds = float(spec.get("seconds", CFG["seconds"]))
    if not 3 <= seconds <= 15:
        raise ValueError(f"seconds 超出模型范围 3~15: {seconds}")
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
    if mode == "t2i":
        vals["width"] = job["width"]
        vals["height"] = job["height"]  # 模板默认 1024², spec 可传(16MP 内任意 32px 对齐)
    if mode == "edit":
        refs = spec.get("references") or []
        if not refs:
            raise ValueError("edit 需要一张源图(--ref 源图=image)")
        vals["ref0"] = refs[0]["path"]  # qwen21t: image_1=画布; 第二张=材料(image_2, <image2> 引用)
        if len(refs) > 1:
            vals["ref1"] = refs[1]["path"]
    if mode == "fl2v":
        kfs = spec.get("keyframes") or []
        first = next((k["path"] for k in kfs if int(k.get("index", 0)) == 0), None)
        last = next((k["path"] for k in kfs if int(k.get("index", 0)) == -1), None)
        if not first:
            raise ValueError("fl2v 需要 index=0 的首帧")
        vals.update(first=first, last=last,
                    width=job["width"], height=job["height"])
        # tao 模板把时长接到参考节点; lora_strength 供 TaoMate A/B(基线传 0)
        vals["length"] = frames_for(seconds)
        vals["lora_strength"] = float(spec.get("lora_strength", 1.0))
    if mode == "ref2v":
        refs = spec.get("references") or []
        if not refs:
            raise ValueError("ref2v 至少需要一张参考图")
        if len(refs) > tpl.get("max_refs", 3):
            raise ValueError(f"ref2v 模板当前最多 {tpl['max_refs']} 张参考图")
        vals["ref0"] = refs[0]["path"]
        if len(refs) > 1:
            vals["ref1"] = refs[1]["path"]
        # 参考接线动态整形(turbo/pika 模板同构: 节点5/6 LoadImage + 节点7 ref_images 列表)
        n7 = next((n for n, x in wf.items()
                   if x.get("class_type") == "MiniMaxH3ReferenceToVideo"), None)
        if n7:
            imgs = wf[n7]["inputs"]["ref_images"]
            if len(refs) == 1:  # 单参考: 拆掉孤儿 LoadImage(6) 防文件不存在炸整图(同 qwen21t 缺参断链)
                wf.pop("6", None)
                wf[n7]["inputs"]["ref_images"] = imgs[:1]
            elif len(refs) > 2:  # 第三参考: 动态挂 LoadImage(18) 接入 ref_images(免改模板)
                wf["18"] = {"class_type": "LoadImage", "inputs": {"image": refs[2]["path"]}}
                wf[n7]["inputs"]["ref_images"] = imgs[:2] + [["18", 0]]
        # omni/pika 模板把时长/分辨率接到参考节点(与 t2v 同法)
        vals["length"] = frames_for(seconds)
        vals["width"] = job["width"]
        vals["height"] = job["height"]
    vals["prefix"] = f"H3/{job['_outdir']}"
    if spec.get("lora"):  # turbo fl2v: 8步/4步 768p 切换(坑㉘同源: 值为文件名字符串)
        vals["lora_name"] = spec["lora"]
    inject(tpl, wf, **vals)
    # 可选参数缺省时断链: 模板预置的支路(如 qwen21t edit 的 image_2 材料槽 LoadImage)
    # 若不拆掉, 孤儿 LoadImage 会因文件不存在炸整图。声明式: optional = {key: ([节点], [(节点, 输入键)])}
    for key, (drop_nodes, drop_links) in (tpl.get("optional") or {}).items():
        if vals.get(key) is None:
            for n in drop_nodes:
                wf.pop(n, None)
            for nid, k in drop_links:
                if nid in wf:
                    wf[nid]["inputs"].pop(k, None)

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
    """轮询 ComfyUI /history → job 状态机（3s 间隔；agent 主契约由 /status 提供）。
    每轮整段 try/except —— 2026-11-22 实测: ComfyUI 重启窗口内查询失败会抛异常,
    无保护的轮询线程静默死亡, 之后所有 job 永远 queued(running), 收结果环节全堵。"""
    est = LAST_CLIP_SEC[0] or 170.0
    while not POLL_STOP.is_set():
        POLL_STOP.wait(3)
        try:
            _poll_once(est)
        except Exception as e:  # noqa: BLE001
            log(f"[poll] 轮询异常(下轮重试): {e}")


def _poll_once(est):
    for job in list(JOBS.values()):
        if job["state"] not in ("queued", "running") or not job["prompt_id"]:
            continue
        st, hist = comfy_json(f"/history/{job['prompt_id']}", timeout=10)
        entry = (hist or {}).get(job["prompt_id"])
        # 僵尸兜底: running 超 20min 且 history 查无此任务(如 ComfyUI 重启丢队列) → 标 failed
        if (not entry and job["state"] == "running" and job.get("started_at")
                and time.time() - _parse_ts(job["started_at"]) > 1200):
            job["state"] = "failed"
            job["error"] = "history 查无此任务(疑似 ComfyUI 重启丢失), 20min 超时兜底"
            job["finished_at"] = now_iso()
            emit("job_failed", job_id=job["id"], error=job["error"])
            log(f"[job {job['id']}] FAILED (zombie)")
            continue
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
