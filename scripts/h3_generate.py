#!/usr/bin/env python3
"""H3 出片 CLI（v0.3 agent 契约）: 只传参数 → 常驻 server 排队/推理 → 经隧道下载成片。

工作流 JSON / 节点寻址(--set)已随 ComfyUI 视频线一起退役（旧版在 v0.2 分支）。

用法:
  python3 scripts/h3_generate.py --mode t2v --prompt "一只橘猫在雪地里奔跑，电影感" \
      [--seed 42] [--seconds 5] [--width 832] [--height 480] [--name demo] [--json]

  首尾帧(fl2v): --mode fl2v --keyframe first.jpg=0 --keyframe last.jpg=-1 --prompt "镜头缓推"
  参考生成(ref2v): --mode ref2v --ref person.jpg=image --ref demo.mp4=video --prompt "..."

参数:
  --mode t2v|fl2v|ref2v     生成模式（t2v=文生视频 / fl2v=首尾帧 / ref2v=参考）
  --prompt "..."            提示词（必填）
  --seed 42                 随机种子（复现用）
  --seconds 5               时长 4~15 秒（模型范围）
  --width/--height          分辨率（默认 832x480）
  --name demo               产出目录名（默认用 job_id）
  --json                    结束时打印完整 job JSON（agent 机器消费）
  --port 8188               本地隧道端口
  --timeout 3600            等待上限（秒）

前置: bash scripts/h3_up.sh（server READY 后才能提交）
产出: outputs/<name>_<时间戳>/ 下全部文件；exit 0=成功 1=失败/超时
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def req(method, path, port, payload=None, raw=None, timeout=30):
    """HTTP 请求 → (status, 解码后的 JSON 或原始 bytes)。"""
    url = f"http://127.0.0.1:{port}{path}"
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    headers = {}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    except urllib.error.URLError as e:
        raise SystemExit(f"✗ 连不上 server({url}): {e.reason}\n  前置: bash scripts/h3_up.sh "
                         f"且 bash scripts/h3_tunnel.sh {port} 端口开隧道")
    try:
        return status, json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, body


def parse_kv(s, what):
    lhs, sep, val = s.partition("=")
    if not sep:
        raise SystemExit(f"✗ {what} 格式应为 路径=值, 收到: {s}")
    return lhs, val


def upload_file(local, port):
    p = Path(local)
    if not p.is_file():
        raise SystemExit(f"✗ 文件不存在: {local}")
    st, resp = req("POST", f"/upload?name={urllib.parse.quote(p.name)}", port, raw=p.read_bytes())
    if st != 200:
        raise SystemExit(f"✗ 上传失败 {local}: {resp}")
    print(f"  ↑ {p.name} → {resp['path']} ({resp['bytes']} B)")
    return resp["path"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="t2v", choices=["t2v", "fl2v", "ref2v"])
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--keyframe", action="append", default=[], metavar="路径=0|-1",
                    help="fl2v: 0=首帧 -1=尾帧, 可重复")
    ap.add_argument("--ref", action="append", default=[], metavar="路径=类型",
                    help="ref2v: image|video|audio, 可重复")
    ap.add_argument("--json", action="store_true", help="结束时打印完整 job JSON")
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--poll", type=float, default=5)
    ap.add_argument("--timeout", type=float, default=3600)
    ARGS = ap.parse_args()

    # 0) server 就绪检查
    st, resp = req("GET", "/health", ARGS.port)
    if st != 200 or resp.get("phase") != "ready":
        raise SystemExit(f"✗ server 未就绪: {resp}\n  前置: bash scripts/h3_up.sh 到 READY-CHECK: PASS")

    # 1) 参数组装（keyframe/ref 先上传）
    keyframes, references = [], []
    for kf in ARGS.keyframe:
        local, idx = parse_kv(kf, "--keyframe")
        keyframes.append({"path": upload_file(local, ARGS.port), "index": int(idx)})
    for rf in ARGS.ref:
        local, typ = parse_kv(rf, "--ref")
        if typ not in ("image", "video", "audio"):
            raise SystemExit(f"✗ --ref 类型须为 image|video|audio, 收到: {typ}")
        references.append({"type": typ, "path": upload_file(local, ARGS.port)})
    if ARGS.mode == "fl2v" and not keyframes:
        raise SystemExit("✗ fl2v 模式至少需要一个 --keyframe")
    if ARGS.mode == "ref2v" and not references:
        raise SystemExit("✗ ref2v 模式至少需要一个 --ref")

    spec = {"mode": ARGS.mode, "prompt": ARGS.prompt, "seed": ARGS.seed,
            "seconds": ARGS.seconds, "name": ARGS.name}
    if ARGS.width:
        spec["width"] = ARGS.width
    if ARGS.height:
        spec["height"] = ARGS.height
    if keyframes:
        spec["keyframes"] = keyframes
    if references:
        spec["references"] = references

    # 2) 提交
    st, resp = req("POST", "/generate", ARGS.port, payload=spec)
    if st != 202:
        raise SystemExit(f"✗ 提交被拒({st}): {resp}")
    jid = resp["job_id"]
    print(f"▶ 已入队 {jid} (mode={ARGS.mode} seed={ARGS.seed} seconds={ARGS.seconds})")

    # 3) 轮询（agent 主契约: /status; 进度行给人类看）
    t0 = time.time()
    while True:
        time.sleep(ARGS.poll)
        st, job = req("GET", f"/status?job_id={urllib.parse.quote(jid)}", ARGS.port)
        if st != 200:
            raise SystemExit(f"✗ 轮询异常({st}): {job}")
        s = job["state"]
        prog = job.get("progress") or {}
        est = f" {prog.get('pct_est', 0):.0f}%" if prog else ""
        print(f"  [{time.time()-t0:6.0f}s] {s}{est} {job.get('error') or ''}")
        if s in ("done", "failed", "cancelled"):
            break
        if time.time() - t0 > ARGS.timeout:
            raise SystemExit(f"✗ 超时(>{ARGS.timeout:.0f}s), 可继续用 /status?job_id={jid} 观察")
    if job["state"] != "done":
        detail = f"\n  错误: {job.get('error')}" if job.get("error") else ""
        raise SystemExit(f"✗ job {job['state']}{detail}")

    # 4) 下载成片（subfolder/type 必传——SaveVideo 带 prefix 时产物在子目录）
    st, res = req("GET", f"/result?job_id={urllib.parse.quote(jid)}", ARGS.port)
    dest = Path("outputs") / f"{ARGS.name or jid}_{time.strftime('%Y%m%d_%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in res.get("files", []):
        qs = urllib.parse.urlencode({"job_id": jid, "name": f["name"],
                                     "subfolder": f.get("subfolder", ""),
                                     "type": f.get("type", "output")})
        st, data = req("GET", f"/files?{qs}", ARGS.port, timeout=300)
        if st != 200 or (isinstance(data, bytes) and len(data) < 1024 and not f["name"].endswith(".mp4")):
            preview = data[:200] if isinstance(data, bytes) else data
            raise SystemExit(f"✗ 下载失败({st}): {f['name']} → {preview}")
        out = dest / f["name"]
        out.write_bytes(data if isinstance(data, bytes) else str(data).encode())
        print(f"  ↓ {out} ({f['bytes']} B)")

    if ARGS.json:
        print(json.dumps(job, ensure_ascii=False, indent=1))
    print(f"✅ 完成 {job['elapsed_s']}s (mode={ARGS.mode} seed={ARGS.seed}) → {dest}")


if __name__ == "__main__":
    main()
