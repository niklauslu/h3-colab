#!/usr/bin/env bash
# 建立本地 SSH 隧道 → Colab ComfyUI (前台运行, Ctrl-C 断开)
# 用法: bash scripts/h3_tunnel.sh [会话名]
#   PORT=8189 VM_PORT=8189 bash scripts/h3_tunnel.sh h3   # 直连 ComfyUI 内核口(A/B 用)
# 成功后本地访问 http://127.0.0.1:$PORT
# 坑⑨: 认证靠 CLI 在桥接时把 ~/.ssh/id_ed25519.pub 注册给 runtime(X-Colab-Ssh-Pubkey header),
#       ssh 侧必须用配套私钥 -i + root 用户, 缺一不可
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
PORT="${PORT:-8188}"
export PATH="$HOME/.local/bin:$PATH"
KEY="$HOME/.ssh/id_ed25519"
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -f "$KEY" -C "colab-ssh" -q
exec ssh -o ProxyCommand="colab ssh --proxy-mode -s $SESSION -i $KEY.pub" \
     -i "$KEY" \
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
     -o ServerAliveInterval=30 -o ServerAliveCountMax=4 \
     -N -L "${PORT}:127.0.0.1:${VM_PORT:-8188}" "root@$SESSION"
