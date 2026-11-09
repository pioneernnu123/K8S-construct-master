#!/usr/bin/env python3
"""Exercise the released controller against a real lightweight monitor process."""
import argparse
import contextlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--platform-source', type=Path, required=True)
    args = parser.parse_args()
    state = {'timestamp': time.time(), 'value': .85, 'down': False}
    lock = threading.Lock()
    class Exporter(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                current = dict(state)
            if current['down']:
                self.send_error(503)
                return
            body = (f"rv_node_cpu_utilization_ratio {current['value']}\n"
                    f"rv_node_sample_timestamp_seconds {current['timestamp']:.3f}\n").encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    exporter = ThreadingHTTPServer(('127.0.0.1', 0), Exporter)
    thread = threading.Thread(target=exporter.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    source = args.platform_source.resolve()/'platform/control'
    sys.path.insert(0, str(source))
    sys.dont_write_bytecode = True
    import scenario
    from common import metric_sample
    class FakeKube:
        def status(self, obj, status):
            obj['status'] = status
    policy = {
        'kind': 'ScenarioPolicy', 'metadata': {'namespace': 'default', 'name': 'smoke', 'uid': 'smoke'},
        'spec': {'query': 'max(rv_node_cpu_utilization_ratio)', 'high': .8, 'low': .3,
                 'consecutiveSamples': 3, 'cooldownSeconds': 0, 'maxMetricAgeSeconds': 45}}
    process = None
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix='rv-monitor-smoke-') as directory:
            config = Path(directory)/'monitor.json'
            config.write_text(json.dumps({'targets': [f'127.0.0.1:{exporter.server_port}'],
                                          'scrapeInterval': '1s', 'scrapeTimeout': '1s'}), encoding='utf-8')
            process = subprocess.Popen([str(args.binary.resolve()), '--config.file='+str(config),
                                        '--web.listen-address=127.0.0.1:'+str(port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for attempt in range(50):
                try:
                    with urlopen(base+'/-/ready', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError('monitor process exited before readiness')
                    time.sleep(.1)
            else:
                raise AssertionError('readiness timeout')
            vector = scenario.query(base, policy['spec']['query'])
            assert metric_sample(vector, time.time()) == .85, vector
            results.append('Released scenario.query and metric_sample accept real HTTP vector')
            controller = scenario.Scenarios(FakeKube(), {'prometheusURL': base})
            controller.step(policy)
            assert policy['status']['consecutive'] == 1, policy
            time.sleep(1.2)
            controller.step(policy)
            assert policy['status']['consecutive'] == 1, policy
            results.append('Frozen exporter sampling clock is not counted as a new observation')
            with lock:
                state['timestamp'] = time.time()
            time.sleep(1.2)
            controller.step(policy)
            assert policy['status']['consecutive'] == 2, policy
            results.append('Fresh source observation advances the existing policy')
            with lock:
                state['down'] = True
            for attempt in range(30):
                if not scenario.query(base, policy['spec']['query']):
                    break
                time.sleep(.1)
            else:
                raise AssertionError('failed node remains in current query result')
            results.append('Failed node metrics stop contributing; no synthetic zero CPU')
    finally:
        if process is not None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
                process.wait()
        exporter.shutdown()
        exporter.server_close()
    print(json.dumps({'passed': True, 'checks': results}, indent=2))

if __name__ == '__main__':
    main()
