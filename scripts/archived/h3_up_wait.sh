#!/usr/bin/env bash
# A100 抢位循环：轮流尝试标准/High-RAM 分配，抢到后自动完成整站拉起。
# 用法: bash scripts/h3_up_wait.sh [会话名] [最大轮数(默认18)]
# 坑⑮: A100 高峰期 /tun/m/assign 返回 503（标准与 hm 都可能无容量），只能轮询等位。
set -uo pipefail
SESSION="${1:-${SESSION:-h3}}"
MAX="${2:-18}"
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

try_new() {  # $1=extra flags; 成功输出 READY 并返回 0
  colab new -s "$SESSION" $1 2>&1 | grep -qa "Session READY"
}

for i in $(seq 1 "$MAX"); do
  echo "[wait $i/$MAX] $(date +%H:%M) 尝试标准 A100 …"
  if colab status -s "$SESSION" 2>&1 | grep -qi "not found"; then
    if try_new "--gpu A100"; then
      echo "GOT: standard A100"; break
    fi
    sleep 20
    echo "[wait $i/$MAX] 尝试 High-RAM A100 …"
    if try_new "--gpu A100 --high-mem"; then
      echo "GOT: highmem A100"; break
    fi
  else
    echo "GOT: 会话已存在"; break
  fi
  echo "[wait $i/$MAX] 两种形状都无容量，10 分钟后再试"; sleep 600
done

if colab status -s "$SESSION" 2>&1 | grep -qi "not found"; then
  echo "FAILED: $MAX 轮均未抢到 A100 容量（高峰持续）。建议明早低峰重试或改用 L4。"
  exit 1
fi

echo "▶ 容量到手，完成整站拉起 …"
bash "$PROJ/scripts/h3_up.sh" "$SESSION"
