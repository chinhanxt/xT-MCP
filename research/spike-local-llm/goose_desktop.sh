#!/bin/bash
D=/tmp/xt-spike-local
export XDG_CONFIG_HOME=$D/goose-home/config XDG_DATA_HOME=$D/goose-home/data XDG_STATE_HOME=$D/goose-home/state
export GOOSE_PROVIDER=ollama GOOSE_MODEL=qwen3.5:4b OLLAMA_HOST=http://127.0.0.1:11434 DISPLAY=:1
exec $D/goose-desktop/usr/lib/goose/Goose --no-sandbox --user-data-dir=$D/goose-home/electron "$@"
