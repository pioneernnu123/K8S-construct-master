#!/usr/bin/env python3
import json
import os
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from support import run, Skip


def measure(t, variant, workers, trial):
    t.apply(variant + '.yaml')
    name = 'mpi-fusion-' + variant
    def completed():
        job = t.get('mpijob', name)
        conditions = job.get('status', {}).get('conditions', [])
        if any(c['type'] == 'Failed' and c['status'] == 'True' for c in conditions):
            raise AssertionError(job['status'])
        return job if any(c['type'] == 'Succeeded' and c['status'] == 'True' for c in conditions) else None
    job = t.wait(completed, name + ' Succeeded')
    pods = t.items('pods')
    launchers = [p for p in pods if p['metadata']['name'].startswith(name + '-launcher-')]
    assert len(launchers) == 1, launchers
    logs = t.cmd('logs', '-n', t.ns, launchers[0]['metadata']['name'], '-c', 'mpi')
    (t.directory / ('pair-%d-%s.log' % (trial, variant))).write_text(logs, encoding='utf-8')
    messages = [json.loads(line) for line in logs.splitlines() if line.startswith('{') and '"operation"' in line]
    assert len(messages) == 1, logs
    result = messages[0]
    assert result['correct'] is True and result['ranks'] == workers * 2, result
    assert result['mantissa_bits'] >= 113, result
    worker_pods = [p for p in pods if p['metadata']['name'].startswith(name + '-worker-')]
    assert len(worker_pods) == workers
    assert len({p['spec']['nodeName'] for p in worker_pods}) == workers
    assert sorted(result['worker_pod_names']) == sorted([p['metadata']['name'] for p in worker_pods] * 2)
    images = [c['imageID'] for p in worker_pods + launchers for c in p['status']['containerStatuses']]
    result['imageDigest'] = t.same_images(images)
    result['workerNodes'] = sorted(p['spec']['nodeName'] for p in worker_pods)
    result['launcherNode'] = launchers[0]['spec']['nodeName']
    for pod in worker_pods:
        limits = pod['spec']['containers'][0]['resources']['limits']
        assert limits['memory'] == '1Gi', limits
        assert str(limits['cpu']) == ('1' if variant.endswith('-limited') else '2'), limits
    t.record('fusion-measurement', trial=trial, variant=variant, result=result, mpijob=job,
             workerPlacement={p['metadata']['name']: p['spec']['nodeName'] for p in worker_pods},
             admittedWorkerResources=[p['spec']['containers'][0]['resources'] for p in worker_pods])
    t.cmd('delete', 'mpijob', name, '-n', t.ns, '--wait=true', '--timeout=180s')
    t.wait(lambda: not any(p['metadata']['name'].startswith(name + '-') for p in t.items('pods')),
           'previous trial Pods released')
    t.wait(lambda: t.get('service', name, missing=True) is None, 'previous trial Service released')
    return result


def test(t):
    workers = int(os.environ.get('MPI_WORKERS', '1'))
    pairs = 1
    if workers < 1:
        raise ValueError('MPI_WORKERS >= 1 required')
    t.values.update(MPI_WORKERS=str(workers), MPI_RANKS=str(workers * 2))
    available = [n for n in t.items('nodes', cluster=True)
                 if n['metadata']['labels'].get('kubernetes.io/arch') == 'riscv64'
                 and not n['spec'].get('unschedulable')
                 and any(c['type'] == 'Ready' and c['status'] == 'True' for c in n['status']['conditions'])]
    if workers > len(available):
        raise Skip('not enough physical RISC-V nodes for MPI worker anti-affinity')
    rounds = []
    for trial in range(pairs):
        order = ['serial', 'overlap', 'serial-limited', 'overlap-limited']
        if trial % 2:
            order.reverse()
        rounds.append({v: measure(t, v, workers, trial + 1) for v in order})
    t.record(
        'fusion-functional-decision',
        passed=True,
        variants=['serial', 'overlap', 'serial-limited', 'overlap-limited'],
        performanceEvaluated=False,
        performanceImprovementClaimed=False,
        physicalWorkerCount=workers,
    )


if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
