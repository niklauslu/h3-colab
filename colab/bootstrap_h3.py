"""H3 生产管线 · Colab VM 端引导脚本（fire-and-poll 架构）

背景: colab-cli 的 exec 对长阻塞任务不可靠(连接易断, 内核侧任务随之夭折)。
因此所有重活在一个后台线程内顺序执行, 只写日志; exec 只做"提交"与"轮询"。

由本地 `colab exec` 传入 kernel 执行, 用 run_name 选择阶段:

    import runpy; runpy.run_path("/content/h3/bootstrap_h3.py", run_name="setup")    # 提交全套(立即返回)
    import runpy; runpy.run_path("/content/h3/bootstrap_h3.py", run_name="status")   # 轮询进度/健康

setup 线程执行: install(ComfyUI+依赖) → weights(Drive rsync 或 HF 下载) → start(守护循环)
完成写 /content/h3/.setup_done; 全程日志 /content/h3/setup.log
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = "/content/h3"
CUI = "/content/ComfyUI"
DRIVE_ROOT = "/content/drive/MyDrive/H3"
DRIVE_OUTPUTS = f"{DRIVE_ROOT}/outputs"
SETUP_LOG = f"{ROOT}/setup.log"
SETUP_DONE = f"{ROOT}/.setup_done"
GCS_BUCKET = "h3-weights-309005742981"  # 公开读权重桶; 置 None 可关闭 GCS 源

os.makedirs(ROOT, exist_ok=True)


def load_config():
    cfg = {"expose": "ssh", "port": 8188, "weights_profile": "h3video"}
    try:
        with open(f"{ROOT}/config.json") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


# ---- 模型档案: config.json 的 weights_profile 决定拉哪套权重、放哪个子目录 ----
MODEL_PROFILES = {
    # 视频主力: A100(Ampere) INT8 DiT + INT8 编码器 + FP16 视频 VAE + FP32 音频 VAE (~55GB)
    # 跳过 BF16 大文件与 NVFP4(仅 Blackwell)
    "h3video": {
        "dir": "fasth3",
        "files": [
            "diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors",
            "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            "vae/minimax_h3_video_vae_fp16.safetensors",
            "vae/minimax_h3_audio_vae_fp32.safetensors",
        ],
        "hf": ("FastVideo/FastVideo-FastH3-Comfy", None),  # None = patterns 同 files
    },
    # 生图: Z-Image-Turbo (Apache-2.0, 8步无CFG, T4/L4 可跑, ~19GB, 中英文字渲染强)
    # files 用 HF 仓库原生 split_files/ 布局(GCS/Drive 同布局), symlink 时自动剥前缀
    "zimage": {
        "dir": "zimage",
        "files": [
            "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
            "split_files/text_encoders/qwen_3_4b.safetensors",
            "split_files/vae/ae.safetensors",
        ],
        "hf": ("Comfy-Org/z_image_turbo", None),  # patterns 同 files
    },
    # 生图编辑档: Qwen-Image-2.1-Turbo INT8 (8步 CFG1, T4/L4 可跑, ~17.3GB, 编辑/RGBA/16参考/2K)
    # 注: "Turbo+真去审核"社区不存在(AtomicChat 仅改编码器, 实测图像零变化)——此档案即其功能等价物
    "qwen21t": {
        "dir": "qwen21t",
        "files": [
            "diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors",
            "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
            "vae/qwen_image_2.1_vae_bf16.safetensors",
        ],
        "hf": ("Comfy-Org/Qwen-Image-2.1", None),  # patterns 同 files
    },
}
_PROFILE = load_config()["weights_profile"]
if _PROFILE not in MODEL_PROFILES:
    _PROFILE = "h3video"
_P = MODEL_PROFILES[_PROFILE]
LOCAL_MODELS = f"/content/models/{_P['dir']}"
DRIVE_MODELS = f"{DRIVE_ROOT}/models/{_P['dir']}"
WEIGHT_FILES = _P["files"]
HF_SPECS = _P["hf"]  # (repo, patterns) 或 [(repo, patterns), …]
if isinstance(HF_SPECS, tuple):
    HF_SPECS = [HF_SPECS]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(SETUP_LOG, "a") as f:
        f.write(line + "\n")


def sh(cmd, check=False):
    log(f"$ {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"  ! exit={r.returncode} {r.stderr[-500:] if r.stderr else ''}")
        if check:
            raise RuntimeError(f"command failed: {cmd}")
    return r


def find_hf_token():
    for p in (f"{ROOT}/hf_token.txt", f"{DRIVE_ROOT}/hf_token.txt"):
        try:
            t = open(p).read().strip()
            if t:
                return t
        except Exception:
            pass
    return os.environ.get("HF_TOKEN", "")


# ---------------------------------------------------------------- stages

def stage_install():
    r = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader")
    log(f"GPU: {r.stdout.strip() or r.stderr.strip()}")
    if not os.path.exists(f"{CUI}/main.py"):
        sh(f"rm -rf {CUI} && git clone --depth 1 https://github.com/comfyanonymous/ComfyUI {CUI}", check=True)
    else:
        sh(f"git -C {CUI} pull --ff-only || true")
    pip = f"{sys.executable} -m pip install -q"
    sh(f"{pip} -r {CUI}/requirements.txt", check=True)
    sh(f"{pip} -U huggingface_hub || true")
    cfg = load_config()
    if cfg["expose"] == "cloudflared" and not os.path.exists(f"{ROOT}/cloudflared"):
        sh("wget -q -O /content/h3/cloudflared https://github.com/cloudflare/cloudflared/"
           "releases/latest/download/cloudflared-linux-amd64 && chmod +x /content/h3/cloudflared")
    for d in ("diffusion_models", "text_encoders", "vae", "loras"):
        os.makedirs(f"{CUI}/models/{d}", exist_ok=True)
    log("[install] OK")


CLASSIFY = [  # (目标子目录, 文件名小写含任一关键字), 顺序即优先级
    ("vae", ["vae"]),
    ("loras", ["lora"]),
    ("text_encoders", ["text_encoder", "text-encoder", "qwen", "t5", "clip_l", "clip-l", "mmproj"]),
]
WEIGHT_EXT = (".safetensors", ".sft", ".gguf", ".ckpt", ".pt", ".bin")


def classify(relpath):
    n = relpath.lower()
    if n.endswith((".json", ".txt", ".md", ".model")):
        return "vae" if ("vae" in n and n.endswith(".json")) else None
    # 坑⑱: HF 仓库已带规范子目录时直接采信, 避免关键字误判
    # (如 qwen_image_2.1_turbo DiT 含 "qwen" 会被误归 text_encoders)
    top = n.split("/")[0]
    if top in ("diffusion_models", "text_encoders", "vae", "loras", "model_patches"):
        return top if top != "model_patches" else "diffusion_models"
    if n == "ae.safetensors" or n.endswith("/ae.safetensors"):
        return "vae"  # Z-Image VAE 固定名 ae.safetensors, 无关键字可匹配
    for sub, keys in CLASSIFY:
        if any(k in n for k in keys):
            return sub
    return "diffusion_models" if n.endswith(WEIGHT_EXT) else None


def link_into_comfyui():
    made = 0
    for base, _dirs, files in os.walk(LOCAL_MODELS):
        for fn in files:
            src = os.path.join(base, fn)
            rel = os.path.relpath(src, LOCAL_MODELS)
            # HF 快照保留仓库 split_files/ 层级; 剥掉前缀, 三种来源(GCS/Drive/HF)文件名保持一致
            parts = rel.split(os.sep)
            if parts[0] == "split_files":
                rel = os.path.join(*parts[1:])
            sub = classify(rel)
            if not sub:
                continue
            dst = os.path.join(CUI, "models", sub, rel.replace("/", "__"))
            if os.path.islink(dst) or os.path.exists(dst):
                continue
            os.symlink(src, dst)
            made += 1
    log(f"[weights] symlinked {made} files into ComfyUI/models/*")


def rsync(src, dst):
    # 坑⑧: rsync 不会自动创建多级父目录, 必须先 mkdir -p
    sh(f"mkdir -p '{dst}' && rsync -a '{src}/' '{dst}/'", check=True)


def rsync_parallel(src, dst, jobs=4):
    """按文件并行 rsync(4 路), 带断点续传(--append-verify)。
    冷启动实测: FUSE 单流 ~16-31MB/s, 4 文件并行可显著缩短冷启动。"""
    sh(f"mkdir -p '{dst}'")
    files = [os.path.join(dp, f) for dp, _, fs in os.walk(src) for f in fs]
    procs = []
    for f in files:
        rel = os.path.relpath(f, src)
        ddir = os.path.dirname(os.path.join(dst, rel))
        procs.append(subprocess.Popen(
            ["bash", "-c",
             f"mkdir -p '{ddir}' && rsync -a --partial --append-verify '{f}' '{os.path.join(dst, rel)}'"]))
        if len(procs) >= jobs:
            for p in procs:
                p.wait()
            procs = []
    for p in procs:
        p.wait()
    if any(p.returncode != 0 for p in procs):
        raise RuntimeError("parallel rsync failed")


def gcs_url(rel):
    return f"https://storage.googleapis.com/{GCS_BUCKET}/{rel}"


def gcs_available():
    if not GCS_BUCKET:
        return False
    try:
        req = urllib.request.Request(gcs_url(WEIGHT_FILES[0]), method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def stage_gcs():
    """GCS 公开读直下(HTTP Range 断点续传), 免认证, 4 文件并行(冷启动再省 ~3 分钟)。"""
    import concurrent.futures

    def dl_one(rel):
        dst = os.path.join(LOCAL_MODELS, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(gcs_url(rel), method="HEAD"), timeout=15) as r:
            total = int(r.headers["Content-Length"])
        offset = os.path.getsize(dst) if os.path.exists(dst) else 0
        if offset >= total:
            log(f"  ✓ skip {rel} (已在本地, {total/1e9:.2f} GB)")
            return
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        log(f"  ↓ {rel} ({total/1e9:.2f} GB, 续传自 {offset/1e9:.2f} GB)")
        req = urllib.request.Request(gcs_url(rel), headers=headers)
        mode = "ab" if offset else "wb"
        with urllib.request.urlopen(req, timeout=3600) as r, open(dst, mode) as f:
            while True:
                chunk = r.read(16 << 20)
                if not chunk:
                    break
                f.write(chunk)
        got = os.path.getsize(dst)
        if got != total:
            raise RuntimeError(f"GCS 下载大小不符 {rel}: {got} != {total}")
        log(f"  ✓ {rel}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for fu in concurrent.futures.as_completed([ex.submit(dl_one, r) for r in WEIGHT_FILES]):
            fu.result()


def stage_weights():
    if os.path.exists(f"{LOCAL_MODELS}/.done"):
        link_into_comfyui()
        log("[weights] already ready")
        return
    os.makedirs(LOCAL_MODELS, exist_ok=True)
    if gcs_available():
        log("[weights] source = GCS (fast)")
        stage_gcs()
    elif os.path.isdir(DRIVE_MODELS) and os.listdir(DRIVE_MODELS):
        log(f"[weights] source = Drive ({len(os.listdir(DRIVE_MODELS))} entries), parallel rsync")
        rsync_parallel(DRIVE_MODELS, LOCAL_MODELS, jobs=4)
    else:
        from huggingface_hub import snapshot_download
        token = find_hf_token()
        for repo, patterns in HF_SPECS:
            log(f"[weights] profile={_PROFILE} source = HF {repo}")
            snapshot_download(repo, local_dir=LOCAL_MODELS,
                              allow_patterns=(patterns or WEIGHT_FILES),
                              max_workers=4, token=token or None)
    open(f"{LOCAL_MODELS}/.done", "w").write("ok")
    link_into_comfyui()
    if os.path.isdir(DRIVE_ROOT) and not os.path.exists(f"{DRIVE_MODELS}/.done"):
        log("[weights] writing local -> Drive for reuse")
        rsync_parallel(LOCAL_MODELS, DRIVE_MODELS, jobs=4)
        open(f"{DRIVE_MODELS}/.done", "w").write("ok")
    log("[weights] OK")


DAEMON_SH = r"""#!/bin/bash
PORT=__PORT__
EXPOSE=__EXPOSE__
cd /content/ComfyUI
i=0
fail=0
while true; do
  # 坑⑰: 首次模型装载会阻塞 HTTP, 健康检查须长超时+连续2次失败才重启, 重启后给装载宽限
  if ! curl -s -m 60 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
    fail=$((fail+1))
    if [ $fail -ge 2 ]; then
      echo "[daemon $(date +%T)] comfy down x2, (re)starting" >> /content/h3/daemon.log
      pkill -f "main.py --listen" 2>/dev/null; sleep 2
      nohup python3 main.py --listen 127.0.0.1 --port "${PORT}" \
          >> /content/h3/comfyui.log 2>&1 &
      fail=0
      sleep 90   # 装载宽限: 冷加载可能长时间阻塞响应
    fi
  else
    fail=0
  fi
  if [ "${EXPOSE}" = "cloudflared" ] && [ -x /content/h3/cloudflared ]; then
    if ! pgrep -f cloudflared >/dev/null; then
      nohup /content/h3/cloudflared tunnel --url "http://127.0.0.1:${PORT}" \
          --no-autoupdate > /content/h3/tunnel.log 2>&1 &
    fi
  fi
  sleep 15
  i=$((i+1))
  if [ $((i % 4)) -eq 0 ] && [ -d /content/drive/MyDrive/H3 ]; then
    rsync -a /content/ComfyUI/output/ /content/drive/MyDrive/H3/outputs/ \
        2>>/content/h3/daemon.log
  fi
done
"""


def stage_start():
    cfg = load_config()
    script = DAEMON_SH.replace("__PORT__", str(cfg["port"])).replace("__EXPOSE__", cfg["expose"])
    with open(f"{ROOT}/daemon.sh", "w") as f:
        f.write(script)
    os.chmod(f"{ROOT}/daemon.sh", 0o755)
    os.makedirs("/content/ComfyUI/output", exist_ok=True)
    if os.path.isdir(DRIVE_ROOT):
        os.makedirs(DRIVE_OUTPUTS, exist_ok=True)
    subprocess.Popen(["bash", f"{ROOT}/daemon.sh"], start_new_session=True,
                     stdout=open(f"{ROOT}/daemon.log", "a"), stderr=subprocess.STDOUT)
    log(f"[start] daemon launched (expose={cfg['expose']}, port={cfg['port']})")


def _bg_setup():
    try:
        stage_install()
        stage_weights()
        stage_start()
        open(SETUP_DONE, "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
        log("SETUP DONE ✔")
    except Exception as e:  # noqa: BLE001
        log(f"SETUP FAILED: {e}")


# ---------------------------------------------------------------- phases

def phase_setup():
    if os.path.exists(SETUP_DONE):
        log("setup already done, skip (delete .setup_done to redo)")
        return
    threading.Thread(target=_bg_setup, daemon=True).start()
    print("setup thread started; poll: run_name='status'")


def phase_status():
    port = load_config()["port"]
    r = sh_quiet("nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader")
    print("GPU:", r.strip() or "n/a")
    print("profile:", _PROFILE, "| local models:", LOCAL_MODELS)
    print("setup:", "DONE" if os.path.exists(SETUP_DONE) else "RUNNING")
    if os.path.exists(SETUP_LOG):
        print("---- setup.log tail ----")
        print("".join(open(SETUP_LOG).readlines()[-8:]))
    ok = False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=3) as resp:
            info = json.loads(resp.read())
        dv = info.get("devices", [{}])[0]
        print(f"comfy: UP  vram_free={dv.get('vram_free')} / {dv.get('vram_total')}")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/queue", timeout=3) as resp:
            q = json.loads(resp.read())
        print(f"queue: running={len(q.get('queue_running', []))} pending={len(q.get('queue_pending', []))}")
        ok = True
    except Exception as e:  # noqa: BLE001
        print("comfy: down", e)
    if os.path.exists(f"{ROOT}/tunnel.log"):
        for line in open(f"{ROOT}/tunnel.log"):
            if "trycloudflare.com" in line:
                print("cloudflared URL:", line.strip())
                break
    print("READY-CHECK:", "PASS" if (ok and os.path.exists(SETUP_DONE)) else "WAIT")
    print(f"hint: 本地访问 http://127.0.0.1:{port}（经 SSH 隧道时）")


def sh_quiet(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()


if __name__ in ("setup", "status", "install", "weights", "start", "__main__"):
    fn = {"setup": phase_setup, "status": phase_status}.get(__name__)
    if not fn:  # 兼容旧阶段名: 顺序单跑(不推荐, 仅供手动修复)
        legacy = {"install": stage_install, "weights": stage_weights, "start": stage_start}
        legacy.get(__name__, lambda: print(f"unknown phase {__name__!r}"))()
    else:
        fn()
