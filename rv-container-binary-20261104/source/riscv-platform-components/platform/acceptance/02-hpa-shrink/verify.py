#!/usr/bin/env python3
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from autoscale_case import start, verify_scale
from support import run


def test(t):
    start(t)
    verify_scale(t, expanding=True)
    for pod in t.items('pods'):
        if pod.get('status', {}).get('phase') == 'Running':
            t.cmd('exec', '-n', t.ns, pod['metadata']['name'], '--', '/bin/rv-test-workload', 'marker', 'off')
    verify_scale(t, expanding=False)


if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
