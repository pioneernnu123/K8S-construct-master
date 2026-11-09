#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
task_release=$(cd "$task_dir/../../.." && pwd)
task_role=${1:-help}; shift || true
case "$task_role" in
 resource|network-host|scenario) exec python3 -B "$task_release/tools/install.py" controller --config "${1:?configuration required}" ;;
 node-agent) exec python3 -B "$task_release/tools/install.py" node-agent ;;
 ecosystem) exec python3 -B "$task_release/tools/install.py" monitoring --config "${1:?configuration required}" ;;
 collective|fusion) exec bash "$task_dir/communication/build.sh" "$@" ;;
 check) exec python3 -B "$task_release/tools/release.py" preflight ;;
 *) echo 'Use tools/install.py --help and README.txt in the release root.'; exit 2 ;;
esac
