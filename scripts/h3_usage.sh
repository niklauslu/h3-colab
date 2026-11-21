#!/usr/bin/env bash
# 查看 CU 余额/费率。用法: bash scripts/h3_usage.sh
set -euo pipefail
colab usage
cat <<'EOF'

参考: A100 40GB ≈ 5.4 CU/h; FastH3 INT8 4步 ≈ 2.6 min/条(5s)
      100 CU ≈ 18h A100 ≈ 理论 200~300 条/月
EOF
