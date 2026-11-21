#!/usr/bin/env python3
"""H3 出片脚本: 本地直连 ComfyUI API(经 SSH 隧道), 排队 → 轮询 → 下载成片。

用法:
  python3 scripts/h3_generate.py --workflow workflows/t2v_api.json \
      --set "6.text=一只橘猫在雪地里奔跑, 电影感" \
      --set "60.seed=12345" \
      --name demo [--port 8188] [--timeout 1800] [--out outputs]

--set 节点ID.输入键=值   可重复; 值自动尝试 int/float/bool, 否则按字符串
产出: outputs/<name>_<时间戳>/  (含工作流里全部输出文件)
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def parse_set(s):
    lhs, _, val = s.partition("=")
    node, _, key = lhs.partition(".")
    if not node or not key:
        raise SystemExit(f"--set 格式应为 节点ID.输入键=值, 收到: {s}")
    for cast in (int, float):
        try:
            val = cast(val)
            break
        except ValueError:
            pass
    else:
        if val.lower() in ("true", "false"):
            val = val.lower() == "true"
    return node, key, val


def req(path, payload=None):
    url = f"http://127.0.0.1:{ARGS.port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_file(name, subfolder, ftype, dest: Path):
    q = urllib.parse.urlencode({"filename": name, "subfolder": subfolder, "type": ftype})
    with urllib.request.urlopen(f"http://127.0.0.1:{ARGS.port}/view?{q}", timeout=120) as resp:
        dest.write_bytes(resp.read())


def main():
    wf = json.loads(Path(ARGS.workflow).read_text())

    for s in ARGS.set or []:
        node, key, val = parse_set(s)
        if node not in wf:
            raise SystemExit(f"工作流里没有节点 {node}(可用节点: {list(wf)[:12]}…)")
        if key not in wf[node].get("inputs", {}):
            raise SystemExit(f"节点 {node} 没有 inputs.{key}(现有: {list(wf[node].get('inputs', {}))})")
        wf[node]["inputs"][key] = val
        print(f"✎ {node}.{key} = {val}")

    cid = str(uuid.uuid4())
    r = req("/prompt", {"prompt": wf, "client_id": cid})
    pid = r.get("prompt_id")
    if not pid:
        raise SystemExit(f"提交失败: {r}")
    print(f"▶ 已排队 prompt_id={pid}, 每 5s 轮询…")

    t0 = time.time()
    history = None
    while time.time() - t0 < ARGS.timeout:
        time.sleep(5)
        h = req(f"/history/{pid}")
        entry = h.get(pid)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            print("✗ 生成失败:")
            for m in status.get("messages", [])[-6:]:
                print("  ", m)
            raise SystemExit(1)
        if entry.get("outputs"):
            history = entry
            break
        print(f"  … {int(time.time() - t0)}s")
    else:
        raise SystemExit(f"✗ 超时({ARGS.timeout}s), 任务可能仍在跑, 稍后用 /history/{pid} 查看")

    outdir = Path(ARGS.out) / f"{ARGS.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for node_out in history["outputs"].values():
        if not isinstance(node_out, dict):
            continue
        for item in node_out.values():
            if not isinstance(item, list):
                continue
            for f in item:
                if isinstance(f, dict) and f.get("filename"):
                        dest = outdir / f["filename"]
                        fetch_file(f["filename"], f.get("subfolder", ""), f.get("type", "output"), dest)
                        print(f"⬇ {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
                        n += 1
    if n == 0:
        print("⚠ 未发现输出文件, outputs:", json.dumps(history["outputs"], ensure_ascii=False)[:1500])
    else:
        print(f"✅ 完成 {n} 个文件 → {outdir}  (耗时 {int(time.time() - t0)}s)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workflow", required=True, help="API 格式工作流 JSON")
    p.add_argument("--set", action="append", metavar="节点.键=值", help="注入节点输入, 可重复")
    p.add_argument("--name", default="h3", help="输出目录前缀")
    p.add_argument("--out", default="outputs", help="本地输出目录")
    p.add_argument("--port", type=int, default=8188)
    p.add_argument("--timeout", type=int, default=1800, help="单条最长等待秒数")
    ARGS = p.parse_args()
    main()
