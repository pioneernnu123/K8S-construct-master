#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
task_release=$(cd "$task_dir/../../../.." && pwd)
python3 -B "$task_release/tools/release.py" verify
echo 'This delivery uses precompiled rv-monitor-core. No full Prometheus server is built.'
