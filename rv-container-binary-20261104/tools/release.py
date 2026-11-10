#!/usr/bin/env python3
"""Offline delivery integrity and read-only cluster readiness checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def within(root, relative):
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise ValueError('path escapes delivery root: ' + relative)
    return target


def verify(root=ROOT):
    root = Path(root).resolve()
    manifest = json.loads((root / 'RELEASE-MANIFEST.json').read_text(encoding='utf-8'))
    errors = []
    for row in manifest['files']:
        path = within(root, row['path'])
        if not path.is_file() or sha256(path) != row['sha256']:
            errors.append('missing/changed: ' + row['path'])
    for row in manifest['binaries']:
        path = within(root, row['path'])
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            header = stream.read(64)
        if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 243:
            errors.append('not ELF64 RISC-V: ' + row['path'])
    statistics = json.loads((root / 'source/monitor-core/CODE-STATISTICS.json').read_text(encoding='utf-8'))
    if statistics['summary']['third-party']['effectiveLines'] != 3174:
        errors.append('unexpected third-party monitor scope')
    return {'passed': not errors, 'checkedFiles': len(manifest['files']),
            'binaryFiles': len(manifest['binaries']), 'errors': errors,
            'release': manifest['release'], 'monitorThirdPartyEffectiveLines': 3174}


def kube(*args):
    command = ['kubectl']
    if os.environ.get('KUBECONFIG'):
        command += ['--kubeconfig', os.environ['KUBECONFIG']]
    process = subprocess.run(command + list(args), text=True, capture_output=True, timeout=90)
    if process.returncode:
        raise RuntimeError(process.stderr.strip()[:1500])
    return json.loads(process.stdout)


def preflight():
    errors = []
    nodes = kube('get', 'nodes', '-o', 'json')['items']
    ready_workers = [n['metadata']['name'] for n in nodes
                     if n.get('status', {}).get('nodeInfo', {}).get('architecture') == 'riscv64'
                     and any(c['type'] == 'Ready' and c['status'] == 'True'
                             for c in n.get('status', {}).get('conditions', []))]
    control_arch = os.uname().machine.lower()
    ready = []
    if control_arch in ('riscv64', 'riscv'):
        ready.append(os.uname().nodename)
    else:
        errors.append('control server is not RISC-V: ' + control_arch)
    ready.extend(ready_workers)
    if not ready_workers:
        errors.append('no Ready RISC-V worker node')
    for name in ('mpijobs.kubeflow.org', 'resourceclaims.platform.riscv.io',
                 'resourcepolicies.platform.riscv.io', 'nodeprofiles.platform.riscv.io',
                 'scenariopolicies.platform.riscv.io', 'hostnetworkworkloads.platform.riscv.io'):
        try:
            crd = kube('get', 'crd', name, '-o', 'json')
            if name.startswith('mpijobs') and not any(v['name'] == 'v2beta1' and v['served'] for v in crd['spec']['versions']):
                errors.append('MPIJob v2beta1 is not served')
        except Exception as error:
            errors.append(name + ': ' + str(error))
    try:
        monitor_url = os.environ['PROMETHEUS_URL'].rstrip('/')
        query = 'rv_node_pod_memory_collector_up'
        if os.environ.get('WORKER_IP'):
            query += '{instance=' + json.dumps(os.environ['WORKER_IP'] + ':9108') + '}'
        with urlopen(monitor_url + '/api/v1/query?' + urlencode({'query':query}), timeout=10) as response:
            metrics = json.load(response)
        samples = metrics['data']['result']
        if metrics['status'] != 'success' or not samples or any(float(s['value'][1]) != 1 for s in samples):
            errors.append('project Pod cgroup collector is not ready on the test worker')
    except Exception as error:
        errors.append('Pod memory collection preflight (source the site configuration): ' + str(error))
    try:
        monitor = kube('get', 'deployment', 'rv-prometheus', '-n',
                       os.environ.get('MONITORING_NAMESPACE', 'rv-monitoring'), '-o', 'json')
        containers = monitor['spec']['template']['spec']['containers']
        if not any(c['name'] == 'monitor-core' and c['image'] == 'localhost/rv-monitor-core:0.1.0'
                   for c in containers) or monitor.get('status', {}).get('availableReplicas', 0) < 1:
            errors.append('lightweight monitor is not deployed and available')
    except Exception as error:
        errors.append('lightweight monitor readiness: ' + str(error))
    return {'passed': not errors, 'readyRiscvServers': ready, 'errors': errors,
            'scope': 'read-only prerequisites; not functional or performance acceptance'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'preflight'])
    args = parser.parse_args()
    try:
        result = verify() if args.command == 'verify' else preflight()
    except Exception as error:
        result = {'passed': False, 'errors': [str(error)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
