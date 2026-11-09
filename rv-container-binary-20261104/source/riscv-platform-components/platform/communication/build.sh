#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ ${1:-} == --rebuild ]]; then exec bash "$task_dir/rebuild.sh"; fi
task_release=$(cd "$task_dir/../../../.." && pwd)
(cd "$task_release/offline/communication"; sha256sum -c SHA256SUMS)
echo 'Compiled rv-bench and its MPI libraries are in the bundled communication image.'
echo 'Import with: sudo python3 tools/install.py images; then run the independent MPIJob tests.'
