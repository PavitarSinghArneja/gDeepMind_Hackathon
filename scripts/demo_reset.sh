#!/usr/bin/env bash
# Wipe SnapSort's state and regenerate the mock folders. Only touches this repo's data/, library/, vault/, mock/.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf data library vault mock
python scripts/make_mock_data.py
curl -s http://127.0.0.1:11434/api/tags >/dev/null && echo "Ollama is up." || echo "Start Ollama first: brew services start ollama"
echo "Reset done. Start with: python -m snapsort"
