"""权重预下载 → Google Drive（在 Colab CPU 会话上运行, 由 colab exec 执行）

逐文件下载(峰值本地占用 ≤ 单文件大小) → 校验大小 → 写入 Drive → 删本地暂存。
后台线程执行, 进度写 /content/dl/download.log; 重复运行自动跳过 Drive 已有文件。

    import runpy; runpy.run_path("/content/dl/download_to_drive.py", run_name="main")   # 启动
    import runpy; runpy.run_path("/content/dl/download_to_drive.py", run_name="progress")  # 查进度
"""

import os
import shutil
import subprocess
import threading
import time

REPO = "FastVideo/FastVideo-FastH3-Comfy"
DRIVE = "/content/drive/MyDrive/H3/models/fasth3"
STAGE = "/content/staging"
LOG = "/content/dl/download.log"

FILES = {  # path -> 预期字节(用于校验, 来自 HF tree API 2026-11-20)
    "diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors": 22125781888,
    "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors": 27142856672,
    "vae/minimax_h3_video_vae_fp16.safetensors": 5210505728,
    "vae/minimax_h3_audio_vae_fp32.safetensors": 610000000,  # 约 0.61GB, 宽松校验
}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def du(path):
    try:
        return subprocess.run(["du", "-sm", path], capture_output=True, text=True).stdout.split()[0]
    except Exception:
        return "?"


def job():
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        subprocess.run([os.sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=True)
        from huggingface_hub import snapshot_download

    if not os.path.isdir("/content/drive/MyDrive"):
        log("FATAL: Drive 未挂载 (先 colab drivemount)")
        return
    df = subprocess.run(["df", "-m", "/content"], capture_output=True, text=True).stdout
    log(f"本地磁盘(MB): {df.strip().splitlines()[-1]}")

    os.makedirs(DRIVE, exist_ok=True)
    for i, (rel, expect) in enumerate(FILES.items(), 1):
        dst = os.path.join(DRIVE, rel)
        if os.path.exists(dst) and os.path.getsize(dst) > expect * 0.95:
            log(f"({i}/{len(FILES)}) 已存在于 Drive, 跳过: {rel}")
            continue
        log(f"({i}/{len(FILES)}) 下载: {rel} (期望 ~{expect/1e9:.1f} GB)")
        os.makedirs(STAGE, exist_ok=True)
        snapshot_download(REPO, local_dir=STAGE, allow_patterns=[rel], max_workers=8)
        src = os.path.join(STAGE, rel)
        got = os.path.getsize(src)
        if got < expect * 0.95:
            log(f"FATAL: {rel} 大小不符 got={got} expect={expect}")
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        log(f"  校验通过 {got/1e9:.2f} GB → 写入 Drive …")
        shutil.copyfile(src, dst)
        os.remove(src)
        log(f"  ✓ Drive: {dst} (累计 {du(DRIVE)} MB)")

    open(os.path.join(DRIVE, ".done"), "w").write("ok")
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(DRIVE) for f in fs)
    log(f"ALL DONE: {total/1e9:.1f} GB in Drive, .done 标记已写")
    subprocess.run(["bash", "-c", f"rm -rf {STAGE}"])


if __name__ == "progress":
    if os.path.exists(LOG):
        print("".join(open(LOG).readlines()[-12:]))
    print(f"Drive 已存: {du(DRIVE)} MB | stage: {du(STAGE) if os.path.isdir(STAGE) else 'n/a'}")
else:
    threading.Thread(target=job, daemon=True).start()
    print("download thread started; poll with run_name='progress'")
