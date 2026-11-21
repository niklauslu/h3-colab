#!/usr/bin/env bash
# 一键拉起 H3 工作站(fire-and-poll 架构):
#   创建 A100 → 挂 Drive → 上传引导 → 提交 setup 线程(装环境+权重+启动) → 轮询到就绪
# 用法:  bash scripts/h3_up.sh [会话名]     (默认 h3;  EXPOSE=cloudflared bash ... 切换暴露方式)
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8188}"
EXPOSE="${EXPOSE:-ssh}"   # ssh=本地隧道(推荐,零公网暴露) | cloudflared=备用
export PATH="$HOME/.local/bin:$PATH"

command -v colab >/dev/null || { echo "✗ 未安装 google-colab-cli: uv tool install google-colab-cli"; exit 1; }

echo "▶ 1/6 runtime"
# 坑⑤: colab status 对不存在会话退出码为 0, 必须按输出判断
# 默认标准 A100 (5.4 CU/h, 比 High-RAM 8.88 省 39%); HIGHMEM=1 切大内存机型
if colab status -s "$SESSION" 2>&1 | grep -qi "not found"; then
  if [ "${HIGHMEM:-0}" = "1" ]; then
    colab new -s "$SESSION" --gpu A100 --high-mem
  else
    colab new -s "$SESSION" --gpu A100
  fi
fi
colab status -s "$SESSION" || true

echo "▶ 2/6 挂载 Drive (首次会弹浏览器授权页, 请点击允许)"
mounted=0
for i in 1 2 3; do
  colab drivemount -s "$SESSION" 2>&1 | grep -a "\[colab\]" | head -4 || true
  if colab exec -s "$SESSION" <<<'import os; print(os.path.ismount("/content/drive"))' 2>/dev/null | grep -q True; then
    mounted=1; echo "✅ Drive 已挂载"; break
  fi
  echo "(第 $i 次未挂载, 10s 后重试…)"; sleep 10
done
[ "$mounted" = "1" ] || echo "⚠ Drive 未挂载: 权重将走 HF 下载, 产出无法回写 Drive"

echo "▶ 3/6 上传引导文件"
colab exec -s "$SESSION" <<'PY'
import os,shutil; shutil.rmtree("/content/drive",ignore_errors=True) if os.path.isdir("/content/drive") and not os.path.ismount("/content/drive") else None; os.makedirs("/content/h3",exist_ok=True); print("ready")
PY
colab upload -s "$SESSION" "$PROJ/colab/bootstrap_h3.py" /content/h3/bootstrap_h3.py
cat > /tmp/h3_config.json <<EOF
{"expose": "$EXPOSE", "port": $PORT}
EOF
colab upload -s "$SESSION" /tmp/h3_config.json /content/h3/config.json
[ -f "$PROJ/colab/hf_token.txt" ] && colab upload -s "$SESSION" "$PROJ/colab/hf_token.txt" /content/h3/hf_token.txt || true

echo "▶ 4/6 提交 setup 线程 (装环境+权重+启动, VM 后台执行)"
colab exec -s "$SESSION" <<'PY'
import runpy; _ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="setup")
PY

echo "▶ 5/6 轮询至就绪 (GCS 源 55GB 约 5~8 分钟; Drive 并行 rsync 约 15 分钟; HF 兜底更慢)"
for i in $(seq 1 30); do
  sleep 60
  OUT=$(colab exec -s "$SESSION" <<'PY' 2>&1 || true
import runpy; _ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="status")
PY
)
  echo "$OUT" | grep -a "READY-CHECK\|SETUP FAILED" | head -2
  if echo "$OUT" | grep -q "READY-CHECK: PASS"; then echo "✅ 就绪!"; break; fi
  if echo "$OUT" | grep -q "SETUP FAILED"; then echo "❌ setup 失败, 看 status 日志定位"; break; fi
  echo "… 第 $i 分钟"
done

echo "▶ 6/6 最终状态"
colab exec -s "$SESSION" <<'PY' 2>&1 | grep -av "^{" || true
import runpy; _ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="status")
PY

cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 拉起完成。本地出片隧道(推荐, 前台运行):
  bash scripts/h3_tunnel.sh $SESSION
  然后本地访问 http://127.0.0.1:$PORT
备用: EXPOSE=cloudflared bash scripts/h3_up.sh $SESSION (status 里取 trycloudflare 地址)
出片: python3 scripts/h3_generate.py --workflow workflows/t2v_api.json --set "6.text=提示词"
收工: bash scripts/h3_down.sh $SESSION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
