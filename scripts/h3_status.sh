#!/usr/bin/env bash
# 查看工作站状态(GPU/ComfyUI/队列/权重/日志)。用法: bash scripts/h3_status.sh [会话名]
set -euo pipefail
SESSION="${1:-${SESSION:-h3}}"
colab exec -s "$SESSION" <<'PY'
_ = runpy.run_path("/content/h3/bootstrap_h3.py", run_name="status")
PY
