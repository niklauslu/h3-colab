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
    # v0.3 plan v2: ComfyUI 是唯一能跑 8-step 蒸馏权重的内核(实测结论),
    # 全档案统一 comfyui 运行时; 对外契约由 h3api 外壳(8188)提供, ComfyUI 内部 8189
    # (server 运行时分支归档保留: DiffSynth 支持 v2 架构后可复活, 见 plan-nocomfy.md)
    cfg.setdefault("runtime", "comfyui")
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
    # 视频加速 A/B 档 (v0.3 D1'): PAI PDD Acc LoRA 套 Comfy-Org 底模 pruned INT8
    # 8 步免 CFG; 节点 MiniMaxH3PDDAccApply 读 models/pdd_acc/, nfe 支持 8/4(官方合法)
    # ⚠️ 不可叠 FastH3 v2 蒸馏——本档案 DiT 换底模 pruned, 与 h3video 互斥
    "h3pdd": {
        "dir": "fasth3",
        "files": [
            "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            "vae/minimax_h3_video_vae_fp16.safetensors",
            "vae/minimax_h3_audio_vae_fp32.safetensors",
            "MiniMax-H3-FL2VA-Acc-8Step.safetensors",
            "MiniMax-H3-Ref2VA-Acc-8Step.safetensors",
        ],
        "hf": [("Comfy-Org/MiniMax-H3",
                ["diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                 "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                 "vae/minimax_h3_video_vae_fp16.safetensors",
                 "vae/minimax_h3_audio_vae_fp32.safetensors"]),
               ("alibaba-pai/MiniMax-H3-Acc-LoRAs",
                ["MiniMax-H3-FL2VA-Acc-8Step.safetensors",
                 "MiniMax-H3-Ref2VA-Acc-8Step.safetensors"])],
        "custom_nodes": ["https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc"],
    },
}
_PROFILE = load_config()["weights_profile"]
if _PROFILE not in MODEL_PROFILES:
    _PROFILE = "h3video"
_P = MODEL_PROFILES[_PROFILE]
LOCAL_MODELS = f"/content/models/{_P['dir']}"
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
    for p in (f"{ROOT}/hf_token.txt",):
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
    for d in ("diffusion_models", "text_encoders", "vae", "loras", "pdd_acc"):
        os.makedirs(f"{CUI}/models/{d}", exist_ok=True)
    # 档案自带的自定义节点（h3pdd → PDD Acc 节点, 要求 ComfyUI ≥0.33, 新克隆满足）
    for url in (_P.get("custom_nodes") or []):
        name = url.rstrip("/").split("/")[-1]
        d = f"{CUI}/custom_nodes/{name}"
        if not os.path.exists(d):
            sh(f"git clone --depth 1 {url} {d}", check=True)
            log(f"[install] custom node cloned: {name}")
    log("[install] OK")


CLASSIFY = [  # (目标子目录, 文件名小写含任一关键字), 顺序即优先级
    ("pdd_acc", ["acc-8step"]),  # PDD 头库 LoRA: PDD 节点从 pdd_acc 文件夹读, 不能进 loras
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


def _link_if_comfyui():
    """server 运行时无 ComfyUI, symlink 仅在 ComfyUI 存在时执行(生图档案路径)。"""
    if os.path.exists(f"{CUI}/main.py"):
        link_into_comfyui()


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


def gcs_fast_enough(min_mbps=30.0):
    """坑㉒: 桶在 US-EAST1 而 Colab VM 可能分到 us-west4/asia 等,
    跨区实测仅 ~13MB/s 反而比 HF(52MB/s) 慢 → 先下 32MB 测速再定源"""
    try:
        req = urllib.request.Request(gcs_url(WEIGHT_FILES[0]), headers={"Range": "bytes=0-33554431"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=60) as r:
            n = 0
            while True:
                chunk = r.read(4 << 20)
                if not chunk:
                    break
                n += len(chunk)
        dt = max(time.time() - t0, 0.1)
        mbps = n / 1e6 / dt
        log(f"[weights] GCS 测速 {mbps:.0f} MB/s (阈值 {min_mbps:.0f})")
        return mbps >= min_mbps
    except Exception as e:  # noqa: BLE001
        log(f"[weights] GCS 测速失败: {e}")
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
        _link_if_comfyui()
        log("[weights] already ready")
        return
    os.makedirs(LOCAL_MODELS, exist_ok=True)
    # 坑㉒结论: HF CDN 对 Colab VM 全区快(52~156MB/s), GCS 直连仅桶同区快(121MB/s),
    # 跨区只有 13~25MB/s → 源优先级改为 HF 优先, GCS/Drive 兜底
    try:
        from huggingface_hub import snapshot_download
        token = find_hf_token()
        for repo, patterns in HF_SPECS:
            log(f"[weights] source = HF {repo}")
            snapshot_download(repo, local_dir=LOCAL_MODELS,
                              allow_patterns=(patterns or WEIGHT_FILES),
                              max_workers=4, token=token or None)
        # HF 成功后校验文件齐全(防部分失败静默通过)
        missing = [rel for rel in WEIGHT_FILES
                   if not os.path.exists(os.path.join(LOCAL_MODELS, rel))
                   and not any(os.path.exists(os.path.join(LOCAL_MODELS, d, os.path.basename(rel)))
                               for d in ("diffusion_models", "text_encoders", "vae", "loras"))]
        if missing:
            raise RuntimeError(f"HF 拉取缺文件: {missing}")
    except Exception as e:  # noqa: BLE001
        log(f"[weights] HF 失败({e}), 降级 GCS")
        if gcs_available():
            log("[weights] fallback source = GCS")
            stage_gcs()
        else:
            raise
    open(f"{LOCAL_MODELS}/.done", "w").write("ok")
    _link_if_comfyui()
    log("[weights] OK")


DAEMON_SH = r"""#!/bin/bash
# v0.3 plan v2: 双进程看护 —— h3api 外壳(对外口 PORT) + ComfyUI 内核(内部口 CPORT)
# 坑⑰硬化版沿用: 长超时+连续2次失败才重启+重启宽限; 外壳轻进程宽限短
PORT=__PORT__
CPORT=__CPORT__
EXPOSE=__EXPOSE__
cd /content/ComfyUI
cfail=0
sfail=0
while true; do
  # ComfyUI 看护: 首次模型装载会阻塞 HTTP → 60s 超时+连续2次失败才重启+90s 装载宽限
  if ! curl -s -m 60 "http://127.0.0.1:${CPORT}/system_stats" >/dev/null 2>&1; then
    cfail=$((cfail+1))
    if [ $cfail -ge 2 ]; then
      echo "[daemon $(date +%T)] comfy down x2, (re)starting" >> /content/h3/daemon.log
      pkill -f "main.py --listen" 2>/dev/null; sleep 2
      nohup python3 main.py --listen 127.0.0.1 --port "${CPORT}" \
          >> /content/h3/comfyui.log 2>&1 &
      cfail=0
      sleep 90   # 装载宽限: 冷加载可能长时间阻塞响应
    fi
  else
    cfail=0
  fi
  # h3api 外壳看护: 轻进程秒起; /health 即便返回 503(comfy 未就绪)也算外壳活着
  if ! curl -s -m 10 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    sfail=$((sfail+1))
    if [ $sfail -ge 2 ]; then
      echo "[daemon $(date +%T)] shell down x2, (re)starting" >> /content/h3/daemon.log
      pkill -f "h3api.py" 2>/dev/null; sleep 2
      nohup python3 /content/h3/h3api.py >> /content/h3/h3api.log 2>&1 &
      sfail=0
      sleep 12
    fi
  else
    sfail=0
  fi
  if [ "${EXPOSE}" = "cloudflared" ] && [ -x /content/h3/cloudflared ]; then
    if ! pgrep -f cloudflared >/dev/null; then
      nohup /content/h3/cloudflared tunnel --url "http://127.0.0.1:${PORT}" \
          --no-autoupdate > /content/h3/tunnel.log 2>&1 &
    fi
  fi
  sleep 15
done
"""


SERVER_DAEMON_SH = r"""#!/bin/bash
# v0.3 server 运行时守护: server 从第 0 秒起就监听 /health(phase=booting 也算活),
# 只有进程真挂了才重启; 重启后权重已在盘, server 内部只重载模型不重下载(坑⑰教训: 宽限要长)
PORT=__PORT__
EXPOSE=__EXPOSE__
fail=0
while true; do
  if ! curl -s -m 30 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    fail=$((fail+1))
    if [ $fail -ge 2 ]; then
      echo "[daemon $(date +%T)] server down x2, (re)starting" >> /content/h3/daemon.log
      pkill -f "server_h3.py" 2>/dev/null; sleep 2
      nohup python3 /content/h3/server_h3.py >> /content/h3/server.log 2>&1 &
      fail=0
      sleep 180   # 重启宽限: 重载模型可达 1-2 分钟
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
done
"""


def stage_start():
    cfg = load_config()
    if cfg["runtime"] == "server":
        if cfg["expose"] == "cloudflared" and not os.path.exists(f"{ROOT}/cloudflared"):
            sh("wget -q -O /content/h3/cloudflared https://github.com/cloudflare/cloudflared/"
               "releases/latest/download/cloudflared-linux-amd64 && chmod +x /content/h3/cloudflared", check=True)
        script = SERVER_DAEMON_SH.replace("__PORT__", str(cfg["port"])).replace("__EXPOSE__", cfg["expose"])
        with open(f"{ROOT}/daemon.sh", "w") as f:
            f.write(script)
        os.chmod(f"{ROOT}/daemon.sh", 0o755)
        os.makedirs(f"{ROOT}/outputs", exist_ok=True)
        subprocess.Popen(["bash", f"{ROOT}/daemon.sh"], start_new_session=True,
                         stdout=open(f"{ROOT}/daemon.log", "a"), stderr=subprocess.STDOUT)
        log(f"[start] server daemon launched (runtime=server, port={cfg['port']}); "
            f"deps/weights/load 由 server 内部编排, 轮询 run_name='status' 看 phase")
        return
    # comfyui 运行时: 双进程 = h3api 外壳(cfg.port 对外) + ComfyUI(comfy_port 内部)
    comfy_port = int(cfg.get("comfy_port", cfg["port"] + 1))
    script = (DAEMON_SH.replace("__PORT__", str(cfg["port"]))
              .replace("__CPORT__", str(comfy_port))
              .replace("__EXPOSE__", cfg["expose"]))
    with open(f"{ROOT}/daemon.sh", "w") as f:
        f.write(script)
    os.chmod(f"{ROOT}/daemon.sh", 0o755)
    os.makedirs("/content/ComfyUI/output", exist_ok=True)
    subprocess.Popen(["bash", f"{ROOT}/daemon.sh"], start_new_session=True,
                     stdout=open(f"{ROOT}/daemon.log", "a"), stderr=subprocess.STDOUT)
    log(f"[start] daemon launched (shell={cfg['port']} → comfy={comfy_port}, "
        f"两进程由守护循环拉起; READY = 外壳 /state phase=ready)")


def _bg_setup():
    try:
        if load_config()["runtime"] == "server":
            # 启动归属反转(plan-nocomfy): deps/weights/load 全在 server 进程内编排,
            # bootstrap 只负责把 server 拉起来; .setup_done 由 server READY 后自写
            stage_start()
        else:
            # 坑㉒优化: install 与 weights 并行(互不依赖), 就绪时间省 ~1 分钟
            t1 = threading.Thread(target=stage_install)
            t2 = threading.Thread(target=stage_weights)
            t1.start(); t2.start(); t1.join(); t2.join()
            stage_start()
            open(SETUP_DONE, "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
        log("SETUP DONE ✔" if os.path.exists(SETUP_DONE) else "SETUP SUBMITTED (server runtime)")
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
    cfg = load_config()
    port = cfg["port"]
    comfy_port = int(cfg.get("comfy_port", port + 1))
    r = sh_quiet("nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader")
    print("GPU:", r.strip() or "n/a")
    print("profile:", _PROFILE, "| runtime:", cfg["runtime"], "| local models:", LOCAL_MODELS)
    print("setup:", "DONE" if os.path.exists(SETUP_DONE) else "RUNNING")
    if cfg["runtime"] == "server":
        ok = _status_server(port)
    else:
        ok = _status_shell(port, comfy_port)
    print("READY-CHECK:", "PASS" if ok else "WAIT")
    print(f"hint: 本地访问 http://127.0.0.1:{port}（经 SSH 隧道时）")


def _status_server(port):
    # server 运行时(归档): 就绪判据 = /state phase == ready
    ok = False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=5) as resp:
            st = json.loads(resp.read())
        print(f"server: phase={st.get('phase')} {st.get('phase_detail') or ''}")
        if st.get("error"):
            print("server error:", st["error"])
        jobs = st.get("jobs") or {}
        if jobs:
            live = {jid: j.get("state") for jid, j in jobs.items()
                    if j.get("state") in ("queued", "running")}
            print(f"jobs: total={len(jobs)} live={live or '{}'}")
        ok = st.get("phase") == "ready"
    except Exception as e:  # noqa: BLE001
        print("server: down", e)
    for lf in (f"{ROOT}/server.log",):
        if os.path.exists(lf):
            print(f"---- {os.path.basename(lf)} tail ----")
            print("".join(open(lf).readlines()[-6:]))
    return ok


def _status_shell(port, comfy_port):
    # comfyui 运行时(默认): 就绪判据 = 外壳 /state phase == ready(含 ComfyUI 可达)
    ok = False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=5) as resp:
            st = json.loads(resp.read())
        print(f"shell: phase={st.get('phase')} {st.get('phase_detail') or ''}")
        jobs = st.get("jobs") or {}
        if jobs:
            live = {jid: j.get("state") for jid, j in jobs.items()
                    if j.get("state") in ("queued", "running")}
            print(f"jobs: total={len(jobs)} live={live or '{}'}")
        ok = st.get("phase") == "ready"
    except Exception as e:  # noqa: BLE001
        print("shell: down", e)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{comfy_port}/system_stats", timeout=5) as resp:
            info = json.loads(resp.read())
        dv = info.get("devices", [{}])[0]
        print(f"comfy: UP  vram_free={dv.get('vram_free')} / {dv.get('vram_total')}")
        with urllib.request.urlopen(f"http://127.0.0.1:{comfy_port}/queue", timeout=5) as resp:
            q = json.loads(resp.read())
        print(f"queue: running={len(q.get('queue_running', []))} pending={len(q.get('queue_pending', []))}")
    except Exception as e:  # noqa: BLE001
        print("comfy: down", e)
    for lf in (f"{ROOT}/h3api.log",):
        if os.path.exists(lf):
            print(f"---- {os.path.basename(lf)} tail ----")
            print("".join(open(lf).readlines()[-6:]))
    return ok


def sh_quiet(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()


if __name__ in ("setup", "status", "install", "weights", "start", "__main__"):
    fn = {"setup": phase_setup, "status": phase_status}.get(__name__)
    if not fn:  # 兼容旧阶段名: 顺序单跑(不推荐, 仅供手动修复)
        legacy = {"install": stage_install, "weights": stage_weights, "start": stage_start}
        legacy.get(__name__, lambda: print(f"unknown phase {__name__!r}"))()
    else:
        fn()
