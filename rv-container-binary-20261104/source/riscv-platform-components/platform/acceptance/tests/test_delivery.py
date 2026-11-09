"""Reject performance failures and invented contribution ratios."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

ACCEPTANCE = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ACCEPTANCE))
from performance import fusion_decision
sys.path.insert(0, str(ROOT / 'tools'))
from contribution import evaluate, valid_url, safe_prefix
from release import within


def measurements():
    row = {}
    for name, seconds in [('serial', 2), ('overlap', 1), ('serial-limited', 3), ('overlap-limited', 2)]:
        row[name] = {'operation': 'fusion', 'mode': name.split('-')[0], 'mean_seconds': seconds,
                     'correct': True, 'count': 4096, 'repeats': 7, 'bytes_per_rank': 65536,
                     'mantissa_bits': 113, 'ranks': 4}
    return [copy.deepcopy(row) for _ in range(3)]


class DeliveryTests(unittest.TestCase):
    def test_fusion_and_resource_effect_pass(self):
        self.assertTrue(fusion_decision(measurements())['passed'])

    def test_slow_fusion_is_not_a_pass(self):
        rows = measurements()
        for row in rows:
            row['overlap']['mean_seconds'] = 3
        self.assertFalse(fusion_decision(rows)['passed'])

    def test_no_resource_effect_is_not_a_pass(self):
        rows = measurements()
        for row in rows:
            row['overlap-limited']['mean_seconds'] = 1
        self.assertFalse(fusion_decision(rows)['passed'])

    def test_mismatched_work_or_nonfinite_timing_is_rejected(self):
        for key, value in [('count', 1), ('mean_seconds', float('nan'))]:
            rows = measurements()
            rows[0]['overlap'][key] = value
            with self.assertRaises(ValueError):
                fusion_decision(rows)

    def test_too_few_pairs_or_disabled_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            fusion_decision(measurements()[:1])
        with self.assertRaises(ValueError):
            fusion_decision(measurements(), 1)

    def test_only_exact_submitted_project_source_counts(self):
        entries = [{'path': 'platform/a.py', 'classification': 'project', 'effectiveLines': 60,
                    'sha256': hashlib.sha256(b'ours').hexdigest()},
                   {'path': 'monitor-core/third_party/x.go', 'classification': 'third-party',
                    'effectiveLines': 40, 'sha256': hashlib.sha256(b'upstream').hexdigest()}]
        scope = {'totalEffectiveLines': 100, 'files': entries}
        good = evaluate(scope, lambda p: b'ours', {'platform': 'src'})
        self.assertEqual(good['contributionRatioPercent'], 60)
        bad = evaluate(scope, lambda p: b'changed', {'platform': 'src'})
        self.assertEqual(bad['contributedEffectiveLines'], 0)
        self.assertFalse(bad['passed'])

    def test_repository_and_delivery_paths_cannot_escape(self):
        for value in ['../private', '/absolute', 'C:\\secret']:
            with self.assertRaises(ValueError):
                safe_prefix(value)
        with self.assertRaises(ValueError):
            within(ROOT, '../outside')
        with self.assertRaises(ValueError):
            valid_url('https://user:password@example.org/repo.git')
        self.assertEqual(safe_prefix('source/platform'), 'source/platform')


if __name__ == '__main__':
    unittest.main()
