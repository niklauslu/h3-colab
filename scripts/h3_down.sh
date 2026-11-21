#!/usr/bin/env bash
# 释放 runtime(权重/产出都在 Drive, 不受影响)。用法: bash scripts/h3_down.sh [会话名]
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
echo "▶ 停止本地隧道(如有)"
pkill -f "ssh .*-N -L.*8188" 2>/dev/null || true
echo "▶ 停止 Colab runtime: $SESSION"
colab stop -s "$SESSION"
echo "✅ 已释放。权重库仍在 Drive:/MyDrive/H3/models 下, 下次 h3_up.sh 秒级恢复。"
