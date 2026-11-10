#!/usr/bin/env python3
"""Verify target-function source against an actual remotely fetched Git commit."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def valid_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('repository must be a public HTTPS URL without credentials, query or fragment')
    return url


def safe_prefix(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value:
        raise ValueError('invalid repository path prefix')
    return '' if path.as_posix() == '.' else path.as_posix()


def git(directory, *args, check=True):
    environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_ASKPASS': ''}
    result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True,
                            timeout=180, env=environment)
    if check and result.returncode:
        raise RuntimeError('git command failed: ' + result.stderr.decode('utf-8', 'replace')[:1000])
    return result


def evaluate(statistics, fetch_blob, mapping):
    # The denominator includes reused monitor source, never just our own files.
    denominator = statistics['totalEffectiveLines']
    if denominator <= 0:
        raise ValueError('empty target-function source scope')
    numerator = 0
    rows = []
    for entry in statistics['files']:
        if entry['classification'] != 'project':
            continue
        component, relative = entry['path'].split('/', 1)
        if component not in mapping:
            rows.append({'path': entry['path'], 'verified': False, 'reason': 'no repository mapping'})
            continue
        repo_path = '/'.join(v for v in (safe_prefix(mapping[component]), relative) if v)
        blob = fetch_blob(repo_path)
        verified = blob is not None and hashlib.sha256(blob).hexdigest() == entry['sha256']
        if verified:
            numerator += entry['effectiveLines']
        rows.append({'path': entry['path'], 'repositoryPath': repo_path, 'verified': verified,
                     'effectiveLines': entry['effectiveLines'], 'sha256': entry['sha256']})
    ratio = numerator * 100 / denominator
    return {'passed': ratio >= 50, 'contributedEffectiveLines': numerator,
            'totalTargetFunctionEffectiveLines': denominator,
            'contributionRatioPercent': round(ratio, 4), 'thresholdPercent': 50, 'files': rows,
            'method': 'exact byte hashes at fetched commit; only project-classified target source in numerator',
            'ownershipNote': 'requires assessor approval of target scope and original authorship; not an authorship determination'}


def verify_remote(config, root=ROOT):
    url = valid_url(config['repositoryUrl'])
    revision = config['revision']
    if not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', revision):
        raise ValueError('pin a full Git commit ID, not a mutable branch/tag')
    mapping = config['mapping']
    if not isinstance(mapping, dict):
        raise ValueError('mapping must be an object')
    statistics = json.loads((Path(root) / 'source/monitor-core/CODE-STATISTICS.json').read_text(encoding='utf-8'))
    # Fetch into a disposable repository. Never modify the developer checkout,
    # upload code, reuse a user-supplied .git, or use cached local commits as proof.
    with tempfile.TemporaryDirectory(prefix='rv-public-contribution-') as temporary:
        git(temporary, 'init', '--quiet')
        git(temporary, '-c', 'credential.helper=', '-c', 'core.askPass=',
            'fetch', '--no-tags', '--depth=1', url, revision)
        commit = git(temporary, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
        if commit.lower() != revision.lower():
            raise ValueError('fetched commit does not match requested revision')
        def blob(path):
            result = git(temporary, 'show', commit + ':' + path, check=False)
            return result.stdout if result.returncode == 0 else None
        result = evaluate(statistics, blob, mapping)
        result.update(repositoryUrl=url, commit=commit, remoteFetched=True,
                      commitCount=int(git(temporary, 'rev-list', '--count', commit).stdout),
                      commitCountScope='shallow fetch: only the pinned submission; not lifetime project history')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_remote(json.loads(args.config.read_text(encoding='utf-8')))
    except Exception as error:
        result = {'passed': False, 'remoteFetched': False, 'error': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
