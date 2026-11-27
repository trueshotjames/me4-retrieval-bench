#!/usr/bin/env bash
# Build the index on a GPU box / cloud VM / Kaggle notebook. NEVER on a Raspberry Pi.
# Usage: tools/remote_index.sh [dataset ...]      (default: all five)
set -euo pipefail
cd "$(dirname "$0")/.."
if grep -qi raspberry /proc/device-tree/model 2>/dev/null && [ "${ME4_ALLOW_INDEX:-0}" != 1 ]; then echo "Refusing: indexing does not run on a Raspberry Pi. Use a cloud machine (ARM cloud VMs are fine)."; exit 2; fi
[ -f adapters/agent_entry.py ] || { echo "Create adapters/agent_entry.py from adapters/agent_entry_template.py first."; exit 2; }
python3 tools/fetch_data.py
python3 -m me4bench build-manifest
python3 -m me4bench index "$@"
python3 tools/verify_index.py index
echo "Copy ./index (and ./data/_manifest) to the Pi, e.g.:  rsync -av index data/_manifest pi@<host>:~/me4-retrieval-bench/"
