#!/usr/bin/env bash
# 建立本地 SSH 隧道 → Colab 工作站 (前台运行, Ctrl-C 断开)
# 用法: bash scripts/h3_tunnel.sh [会话名]
#   PORT=8189 bash scripts/h3_tunnel.sh h3img   # 双站同开时第二个站换本地端口
#   FORWARDS="8188:8188 8189:8189" bash scripts/h3_tunnel.sh h3   # 一条会话多转发
#     （⚠️ 坑㉖: 每个 runtime 只允许一条 SSH 会话(HTTP 429)，多端口必须并进一条连接）
# 成功后本地访问 http://127.0.0.1:$PORT
# 坑⑨: 认证靠 CLI 在桥接时把 ~/.ssh/id_ed25519.pub 注册给 runtime(X-Colab-Ssh-Pubkey header),
#       ssh 侧必须用配套私钥 -i + root 用户, 缺一不可
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
PORT="${PORT:-8188}"
FORWARDS="${FORWARDS:-${PORT}:8188}"   # 本地端口:VM端口 列表（空格分隔）
export PATH="$HOME/.local/bin:$PATH"
KEY="$HOME/.ssh/id_ed25519"
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -f "$KEY" -C "colab-ssh" -q
L_ARGS=""
for fw in $FORWARDS; do L_ARGS="$L_ARGS -L ${fw%%:*}:127.0.0.1:${fw##*:}"; done
exec ssh -o ProxyCommand="colab ssh --proxy-mode -s $SESSION -i $KEY.pub" \
     -i "$KEY" \
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
     -o ServerAliveInterval=30 -o ServerAliveCountMax=4 \
     -N $L_ARGS "root@$SESSION"
