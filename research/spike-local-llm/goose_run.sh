#!/bin/bash
# chạy Goose CLI (Apache-2.0) + Ollama + MCP server spike; cấu hình cô lập trong goose-home
D=/tmp/xt-spike-local
export XDG_CONFIG_HOME=$D/goose-home/config XDG_DATA_HOME=$D/goose-home/data XDG_STATE_HOME=$D/goose-home/state
export OLLAMA_HOST=http://127.0.0.1:11434 GOOSE_PROVIDER=ollama GOOSE_MODEL=${GOOSE_MODEL:-qwen3.5:4b} GOOSE_DISABLE_KEYRING=1
time $D/goose-bin/goose run --no-session --no-profile --max-turns 6 \
  --with-streamable-http-extension "http://127.0.0.1:18220/mcp timeout=60" -t "$1"
