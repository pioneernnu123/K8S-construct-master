#!/usr/bin/env python3
"""Outline 3-2-0013: complete delivery plus real public contribution evidence."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / 'tools'))
from release import verify, preflight
from contribution import verify_remote


def prior_reports(directory, release_hash):
    catalog = json.loads((ROOT / 'source/riscv-platform-components/platform/acceptance/catalog.json').read_text(encoding='utf-8'))[:12]
    rows = []
    for entry in catalog:
        candidates = []
        for file in Path(directory).glob(entry['directory'] + '-*/report.json'):
            obj = json.loads(file.read_text(encoding='utf-8'))
            if obj.get('releaseManifestSha256') == release_hash and obj.get('evidence') == 'real-kubernetes':
                candidates.append((float(obj.get('startedAtEpoch', 0)), file, obj))
        if not candidates:
            rows.append({'outline': entry['outline'], 'status': 'MISSING'})
            continue
        _, file, obj = max(candidates, key=lambda item: item[0])
        rows.append({'outline': entry['outline'], 'status': obj['status'], 'report': str(file)})
    return rows


def main():
    results = Path(os.environ.get('TEST_RESULTS', str(Path.home() / 'rv-test-results/acceptance')))
    output = results / ('13-delivery-contribution-' + str(time.time_ns()))
    output.mkdir(parents=True)
    result = {'case': '13-delivery-contribution', 'outline': '3-2-0013', 'status': 'FAIL',
              'evidence': 'delivery-and-remotely-verified-contribution'}
    try:
        result['integrity'] = verify()
        result['cluster'] = preflight()
        digest = hashlib.sha256((ROOT / 'RELEASE-MANIFEST.json').read_bytes()).hexdigest()
        result['functionalReports'] = prior_reports(results, digest)
        config = os.environ.get('CONTRIBUTION_CONFIG')
        if not config:
            result['contribution'] = {'passed': False, 'reason': 'set CONTRIBUTION_CONFIG to real public submission details'}
        else:
            result['contribution'] = verify_remote(json.loads(Path(config).read_text(encoding='utf-8')))
        passed = (result['integrity']['passed'] and result['cluster']['passed']
                  and all(row['status'] == 'PASS' for row in result['functionalReports'])
                  and result['contribution']['passed'])
        result['status'] = 'PASS' if passed else 'INCOMPLETE'
    except Exception as error:
        result['error'] = str(error)
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('Results: ' + str(output))
    return 0 if result['status'] == 'PASS' else 77 if result['status'] == 'INCOMPLETE' else 1


if __name__ == '__main__':
    sys.exit(main())
