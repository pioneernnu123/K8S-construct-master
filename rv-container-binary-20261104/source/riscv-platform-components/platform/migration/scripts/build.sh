#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ${1:-} == --rebuild ]]; then exec bash "$task_dir/scripts/rebuild.sh"; fi
(cd "$task_dir/bin"; sha256sum -c SHA256SUMS)
echo 'Packaged rv-migrate-runtime verified; no Go toolchain required.'
