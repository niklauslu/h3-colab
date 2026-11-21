#!/usr/bin/env bash
# 一键拉起工作站(fire-and-poll 架构):
#   创建机型 → 挂 Drive → 上传引导 → 提交 setup 线程(装环境+权重+启动) → 轮询到就绪
# 用法:  bash scripts/h3_up.sh [会话名]
#   EXPOSE=cloudflared        切换暴露方式
#   MACHINE=T4                换机型(A100 视频 5.4 CU/h / T4 生图常有免费 / L4 生图 1.67 CU/h)
#   WEIGHTS=zimage            换权重档案(h3video=视频 / zimage=Z-Image-Turbo 生图 ~19GB)
#   生图例: MACHINE=T4 WEIGHTS=zimage bash scripts/h3_up.sh h3img
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8188}"
EXPOSE="${EXPOSE:-ssh}"     # ssh=本地隧道(推荐,零公网暴露) | cloudflared=备用
MACHINE="${MACHINE:-A100}"  # A100=视频主力 | T4/L4=生图轻量档
WEIGHTS="${WEIGHTS:-h3video}"  # 权重档案, 对应 bootstrap_h3.py MODEL_PROFILES
export PATH="$HOME/.local/bin:$PATH"

command -v colab >/dev/null || { echo "✗ 未安装 google-colab-cli: uv tool install google-colab-cli"; exit 1; }

echo "▶ 1/6 runtime"
# 坑⑤: colab status 对不存在会话退出码为 0, 必须按输出判断
# 默认标准 A100 (5.4 CU/h, 比 High-RAM 8.88 省 39%); HIGHMEM=1 切大内存机型(仅 A100 有效)
if colab status -s "$SESSION" 2>&1 | grep -qi "not found"; then
  if [ "${HIGHMEM:-0}" = "1" ] && [ "$MACHINE" = "A100" ]; then
    colab new -s "$SESSION" --gpu "$MACHINE" --high-mem
  else
    colab new -s "$SESSION" --gpu "$MACHINE"
  fi
fi
colab status -s "$SESSION" || true

echo "▶ 2/6 挂载 Drive (首次弹授权页; 90s 内未完成则跳过, 不阻塞——坑㉑)"
# 坑㉑: colab drivemount 在授权完成后可能仍不返回 → 后台跑+看门狗, Drive 仅影响产出回写
colab drivemount -s "$SESSION" >/tmp/h3_drivemount_$SESSION.log 2>&1 &
DM_PID=$!
mounted=0
for i in $(seq 1 9); do
  if colab exec -s "$SESSION" <<<'import os; print(os.path.ismount("/content/drive"))' 2>/dev/null | grep -q True; then
    mounted=1; echo "✅ Drive 已挂载"; break
  fi
  sleep 10
done
if [ "$mounted" != "1" ]; then
  kill $DM_PID 2>/dev/null || true
  echo "⚠ Drive 90s 未挂载, 跳过(不阻塞): 权重走 GCS/HF, 产出暂存本地"
fi

echo "▶ 3/6 上传引导文件"
colab exec -s "$SESSION" <<'PY'
import os,shutil; shutil.rmtree("/content/drive",ignore_errors=True) if os.path.isdir("/content/drive") and not os.path.ismount("/content/drive") else None; os.makedirs("/content/h3",exist_ok=True); print("ready")
PY
colab upload -s "$SESSION" "$PROJ/colab/bootstrap_h3.py" /content/h3/bootstrap_h3.py
cat > /tmp/h3_config.json <<EOF
{"expose": "$EXPOSE", "port": $PORT, "weights_profile": "$WEIGHTS"}
EOF
colab upload -s "$SESSION" /tmp/h3_config.json /content/h3/config.json
[ -f "$PROJ/colab/hf_token.txt" ] && colab upload -s "$SESSION" "$PROJ/colab/hf_token.txt" /content/h3/hf_token.txt || true

echo "▶ 4/6 提交 setup 线程 (装环境+权重+启动, VM 后台执行)"
colab exec -s "$SESSION" <<'PY'
import runpy; _ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="setup")
PY

echo "▶ 5/6 轮询至就绪 (profile=$WEIGHTS; GCS 源最快, Drive 并行 rsync 次之, HF 兜底较慢)"
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

if [ "$WEIGHTS" = "zimage" ]; then
  GEN_CMD='python3 scripts/h3_generate.py --workflow workflows/zimage_t2i_api.json \
      --set "4.text=提示词" --set "6.width=1024" --set "6.height=1024" --name zimg'
else
  GEN_CMD='python3 scripts/h3_generate.py --workflow workflows/t2v_api.json \
      --set "5.text=提示词" --name demo'
fi
cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 拉起完成 (机型=$MACHINE, 权重=$WEIGHTS)。本地隧道(推荐, 前台运行):
  bash scripts/h3_tunnel.sh $SESSION
  然后本地访问 http://127.0.0.1:$PORT
备用: EXPOSE=cloudflared bash scripts/h3_up.sh $SESSION (status 里取 trycloudflare 地址)
出图/出片:
  $GEN_CMD
收工: bash scripts/h3_down.sh $SESSION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
