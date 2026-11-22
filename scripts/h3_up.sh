#!/usr/bin/env bash
# 一键拉起工作站(fire-and-poll 架构):
#   创建机型 → 上传引导 → 提交 setup 线程(装环境+权重+启动) → 轮询到就绪
#   (2026-11-21 起 Drive 整体移除: 权重 HF 优先/GCS 兜底, 产出本地即下; 免授权弹窗免挂载卡死)
# 用法:  bash scripts/h3_up.sh [会话名]
#   EXPOSE=cloudflared        切换暴露方式
#   MACHINE=T4                换机型(A100 视频 5.4 CU/h / T4 生图 1.15 / L4 生图 1.67 CU/h)
#   WEIGHTS=<档案名>          换权重档案(见下表; bootstrap 有机型×档案硬校验, 选错直接止步)
#   生图例: MACHINE=T4 WEIGHTS=zimage bash scripts/h3_up.sh h3img
#
# 站点矩阵 v0.4(官方对齐: 共享底座+双底模+LoRA抽屉; 视频档案 L4/T4 采样段装不下→gate 拦, A100 40G 达标):
#   档案      机型    用途                                增量下载
#   h3pika    A100   视频·ref2v 主力 Pika 6步(112s⭐)     ~53GB
#   h3turbo   A100   视频·官方 turbo(t2v 8步/ref2v 4步)   ~81GB(双底模+抽屉)
#   h3omni    A100   视频·全能单 DiT(后续主力位)          ~52GB
#   h3image   A100   生图·H3 全模态(6步/编辑/16参考)      ~49GB(TE 与视频共享)
#   h3core    A100   共享底座(TE+双VAE 预载)              ~30.5GB
#   zimage    T4/L4  生图·Z-Image(异家族保留)             ~19GB
#   qwen21t   T4/L4  生图·Qwen Turbo(异家族保留)          ~17.3GB
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8188}"
EXPOSE="${EXPOSE:-ssh}"     # ssh=本地隧道(推荐,零公网暴露) | cloudflared=备用
MACHINE="${MACHINE:-A100}"  # A100=视频 | T4/L4=生图(bootstrap gate 会拦错误组合)
WEIGHTS="${WEIGHTS:-h3pika}"  # 权重档案, 对应 bootstrap_h3.py MODEL_PROFILES
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

echo "▶ 2/5 上传引导文件"
colab exec -s "$SESSION" <<'PY'
import os; os.makedirs("/content/h3/workflows",exist_ok=True); print("ready")
PY
colab upload -s "$SESSION" "$PROJ/colab/bootstrap_h3.py" /content/h3/bootstrap_h3.py
[ "$WEIGHTS" = "h3video" ] && colab upload -s "$SESSION" "$PROJ/colab/server_h3.py" /content/h3/server_h3.py
# v0.3 plan v2: 契约外壳 + 工作流模板(节点寻址封装在壳内, agent 不可见)
colab upload -s "$SESSION" "$PROJ/colab/h3api.py" /content/h3/h3api.py
for wf in t2v fl2v ref2v; do
  colab upload -s "$SESSION" "$PROJ/workflows/${wf}_api.json" "/content/h3/workflows/${wf}_api.json"
done
cat > /tmp/h3_config.json <<EOF
{"expose": "$EXPOSE", "port": $PORT, "weights_profile": "$WEIGHTS"}
EOF
colab upload -s "$SESSION" /tmp/h3_config.json /content/h3/config.json
[ -f "$PROJ/colab/hf_token.txt" ] && colab upload -s "$SESSION" "$PROJ/colab/hf_token.txt" /content/h3/hf_token.txt || true

echo "▶ 3/5 提交 setup 线程 (装环境+权重+启动, VM 后台执行)"
colab exec -s "$SESSION" <<'PY'
import runpy; _ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="setup")
PY

echo "▶ 4/5 轮询至就绪 (profile=$WEIGHTS; HF 优先, GCS 兜底)"
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

echo "▶ 5/5 最终状态"
colab exec -s "$SESSION" <<'PY' 2>&1 | grep -av "^{" || true
import runpy; _ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="status")
PY

if [ "$WEIGHTS" = "zimage" ]; then
  GEN_CMD='python3 scripts/h3_generate.py --workflow workflows/zimage_t2i_api.json \
      --set "4.text=提示词" --set "6.width=1024" --set "6.height=1024" --name zimg'
else
  # v0.3: 视频线走常驻 server(runtime=server), agent 契约 CLI 只传参数
  GEN_CMD='python3 scripts/h3_generate.py --mode t2v --prompt "提示词" \
      --seed 42 --seconds 5 --name demo [--json]'
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
