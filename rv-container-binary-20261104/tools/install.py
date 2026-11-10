#!/usr/bin/env python3
"""Role-specific binary installation. No compiler or package-manager downloads."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from release import ROOT, verify

PLATFORM = ROOT / 'source/riscv-platform-components/platform'


def command(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def import_image(path):
    # Stream compressed archives through a private temporary file; do not
    # unpack a container filesystem or run an image as part of installation.
    if path.suffix == '.gz':
        import gzip
        with tempfile.TemporaryDirectory(prefix='rv-image-import-') as temporary:
            target = Path(temporary) / 'image.tar'
            with gzip.open(path, 'rb') as source, target.open('wb') as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
            command('ctr', '-n', 'k8s.io', 'images', 'import', '--platform', 'linux/riscv64', target)
    else:
        command('ctr', '-n', 'k8s.io', 'images', 'import', '--platform', 'linux/riscv64', path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='role', required=True)
    sub.add_parser('images', help='run on every eligible worker; import the three runtime images')
    sub.add_parser('node-agent')
    sub.add_parser('helm', help='install bundled Helm executable')
    for role in ('controller', 'monitoring', 'device', 'migration-controller'):
        part = sub.add_parser(role)
        part.add_argument('--config', type=Path, required=True)
    sub.add_parser('migration-node', help='requires the separately prepared compatible runtime/CRIU')
    args = parser.parse_args()
    result = verify()
    if not result['passed']:
        raise SystemExit(json.dumps(result, ensure_ascii=False))
    if platform.system() != 'Linux' or platform.machine() != 'riscv64':
        raise SystemExit('Install on RISC-V64 Linux; this is not a Windows installer')
    root_roles = {'images', 'node-agent', 'helm', 'controller', 'device', 'migration-controller', 'migration-node'}
    if args.role in root_roles and os.geteuid() != 0:
        raise SystemExit('This role needs sudo/root')
    config = getattr(args, 'config', None)
    if config and not config.is_file():
        raise SystemExit('Missing configuration: ' + str(config))
    if args.role == 'images':
        import_image(ROOT / 'offline/communication/rv-communication-0.2.0.tar.gz')
        import_image(ROOT / 'offline/monitoring/rv-monitor-core-0.1.0-linux-riscv64.oci.tar')
        import_image(ROOT / 'offline/workload/rv-test-workload-20261104-linux-riscv64.oci.tar')
    elif args.role == 'helm':
        with tarfile.open(ROOT / 'offline/monitoring/helm-v3.14.4-linux-riscv64.tar.gz') as archive:
            with archive.extractfile('linux-riscv64/helm') as source, open('/usr/local/bin/helm', 'wb') as destination:
                shutil.copyfileobj(source, destination)
        os.chmod('/usr/local/bin/helm', 0o755)
    elif args.role == 'controller':
        command('bash', PLATFORM / 'control/install.sh', config.resolve())
    elif args.role == 'node-agent':
        command('bash', PLATFORM / 'node/install.sh')
    elif args.role == 'monitoring':
        command(sys.executable, '-B', PLATFORM / 'ecosystem/install.py', config.resolve(), '--offline')
    elif args.role == 'device':
        command('bash', PLATFORM / 'device-plugin/install.sh', config.resolve())
    elif args.role == 'migration-controller':
        command('bash', PLATFORM / 'migration/scripts/install-controller.sh', config.resolve())
    elif args.role == 'migration-node':
        command('bash', PLATFORM / 'migration/scripts/install-node.sh')


if __name__ == '__main__':
    main()
