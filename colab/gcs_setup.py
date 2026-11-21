"""GCS 权重桶初始化 + Drive→GCS 上传（Colab CPU 会话, colab exec 执行）

    import runpy; runpy.run_path("/content/gcs/gcs_setup.py", run_name="main")      # 授权+建桶+启动上传
    import runpy; runpy.run_path("/content/gcs/gcs_setup.py", run_name="progress")  # 查进度

设计:
  - authenticate_user() 期间把 stdout 镜像到 auth.log, 授权链接不因 exec 断连丢失
  - 桶公开读(权重本为公开开源内容), 之后 bootstrap 走免认证 HTTP 下载
  - 上传后台线程, 进度写 upload.log; 按文件校验大小
"""

import json
import os
import subprocess
import sys
import threading
import time

PROJECT = "309005742981"
BUCKET = "h3-weights-309005742981"
LOCATION = "US-EAST1"
SRC = "/content/drive/MyDrive/H3/models/fasth3"
LOG = "/content/gcs/upload.log"
AUTH_LOG = "/content/gcs/auth.log"

FILES = [
    "diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors",
    "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "vae/minimax_h3_video_vae_fp16.safetensors",
    "vae/minimax_h3_audio_vae_fp32.safetensors",
]

# 可覆盖: /content/gcs/upload_spec.json = {"src": "...", "files": [...]}
# (为其他权重档案做种子时用, 如 zimage; 见 docs/manual.md 权重存储节)
try:
    _spec = json.load(open("/content/gcs/upload_spec.json"))
    SRC, FILES = _spec["src"], _spec["files"]
    print(f"[spec] override: src={SRC}, {len(FILES)} files")
except Exception:
    pass

os.makedirs("/content/gcs", exist_ok=True)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass

    def __getattr__(self, name):  # 透传其余属性
        return getattr(self.streams[0], name)


def do_auth():
    from google.colab import auth  # noqa
    old = sys.stdout
    with open(AUTH_LOG, "a") as f:
        sys.stdout = Tee(old, f)
        try:
            auth.authenticate_user()
        finally:
            sys.stdout = old
    print("[auth] done", flush=True)
    with open(AUTH_LOG, "a") as f:
        f.write("[auth] completed\n")


def _bg_upload():
    from google.cloud import storage
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    for i, rel in enumerate(FILES, 1):
        src = os.path.join(SRC, rel)
        want = os.path.getsize(src)
        blob = bucket.blob(rel, chunk_size=128 * 1024 * 1024)
        if blob.exists and blob.size == want:
            log(f"({i}/{len(FILES)}) GCS 已有且大小一致, 跳过: {rel}")
            continue
        log(f"({i}/{len(FILES)}) 上传 {rel} ({want/1e9:.2f} GB) …")
        blob.upload_from_filename(src, timeout=3600)
        got = client.bucket(BUCKET).get_blob(rel).size
        if got != want:
            log(f"FATAL: 大小不符 {rel} got={got} want={want}")
            return
        log(f"  ✓ {rel} ({got/1e9:.2f} GB)")
    open(os.path.join(SRC, ".gcs_synced"), "w").write(BUCKET)
    total = sum(b.size for b in client.list_blobs(BUCKET))
    log(f"ALL DONE: {total/1e9:.2f} GB on gs://{BUCKET}")


if __name__ == "progress":
    for p in (AUTH_LOG, LOG):
        if os.path.exists(p):
            print(f"---- {p} tail ----")
            print("".join(open(p).readlines()[-8:]))
    try:
        from google.cloud import storage
        client = storage.Client(project=PROJECT)
        blobs = list(client.list_blobs(BUCKET)) if client.bucket(BUCKET).exists() else []
        print(f"gs://{BUCKET}: {len(blobs)} blobs, {sum(b.size for b in blobs)/1e9:.2f} GB")
    except Exception as e:  # noqa: BLE001
        print("bucket check:", e)
else:
    # 坑⑫: authenticate_user 会阻塞内核且 headless 下授权 URL 不可达。
    # 改用本地(colab-cli 钥匙串令牌)生成的 authorized_user 凭据文件。
    CREDS = "/content/gcs/gcp_creds.json"
    if os.path.exists(CREDS):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDS
        log("using uploaded user credentials")
    else:
        do_auth()  # 兜底: 浏览器授权
    from google.cloud import storage
    client = storage.Client(project=PROJECT)
    bucket = client.lookup_bucket(BUCKET)
    if bucket is None:
        bucket = client.create_bucket(BUCKET, location=LOCATION)
        log(f"bucket gs://{BUCKET} created @ {LOCATION}")
    else:
        log(f"bucket gs://{BUCKET} 已存在")
    try:  # 公开读(仅对象), 便于 bootstrap 免认证下载
        policy = bucket.get_iam_policy(requested_policy_version=3)
        policy.bindings.append({"role": "roles/storage.objectViewer", "members": ["allUsers"]})
        bucket.set_iam_policy(policy)
        log("public objectViewer set")
    except Exception as e:  # noqa: BLE001
        log(f"publicRead 设置失败(不影响上传): {e}")
    threading.Thread(target=_bg_upload, daemon=True).start()
    print("upload thread started; poll run_name='progress'")
