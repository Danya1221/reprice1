#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "▶️ reprice1: запуск launcher.py" >&2
exec python -u launcher.py
