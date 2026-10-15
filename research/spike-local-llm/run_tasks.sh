#!/bin/bash
# chạy mỗi tác vụ 3 lần với model $1 (mặc định qwen3.5:4b); in JSON mỗi lần
M=${1:-qwen3.5:4b}; shift; EXTRA="$@"
cd /tmp/xt-spike-local
for P in "Ai báo cáo cho Minh?" "Duyệt task T-1"; do
  echo "### $P"; .venv/bin/python scripts/agent.py "$P" --model "$M" -n 3 $EXTRA
done
