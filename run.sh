#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PORT="${1:-8765}"
echo "启动兰德索尔配队工坊： http://127.0.0.1:$PORT"
exec python3 -m pcr_team.server --port "$PORT"
