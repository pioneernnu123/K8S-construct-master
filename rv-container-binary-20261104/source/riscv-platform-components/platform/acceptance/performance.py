"""Paired performance decisions; a measurement below threshold must fail."""
import math
import statistics


def fusion_decision(rounds, minimum_speedup=1.05):
    if not math.isfinite(minimum_speedup) or minimum_speedup <= 1:
        raise ValueError('FUSION_MIN_SPEEDUP must be finite and greater than one')
    if len(rounds) < 3:
        raise ValueError('at least three independent paired runs are required')
    normal, limited = [], []
    for row in rounds:
        times = {}
        for name in ('serial', 'overlap', 'serial-limited', 'overlap-limited'):
            result = row[name]
            seconds = float(result['mean_seconds'])
            if not result.get('correct') or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError('invalid numerical/timing result: ' + name)
            if result['operation'] != 'fusion' or result['mode'] != name.split('-')[0]:
                raise ValueError('operation/mode mismatch')
            times[name] = seconds
        keys = ('count', 'repeats', 'bytes_per_rank', 'mantissa_bits', 'ranks')
        if any(len({row[name][key] for name in times}) != 1 for key in keys):
            raise ValueError('paired runs have different workload parameters')
        for key in ('imageDigest', 'workerNodes', 'launcherNode'):
            if any(row[name].get(key) != row['serial'].get(key) for name in times):
                raise ValueError('paired runs have different image or placement: ' + key)
        normal.append(times['serial'] / times['overlap'])
        limited.append(times['serial-limited'] / times['overlap-limited'])
    speedup = statistics.median(normal)
    constrained = statistics.median(limited)
    return {'passed': speedup >= minimum_speedup and constrained < speedup,
            'medianSpeedup': speedup, 'medianLimitedSpeedup': constrained,
            'minimumSpeedup': minimum_speedup, 'pairSpeedups': normal,
            'limitedPairSpeedups': limited, 'pairs': len(rounds),
            'thresholdOrigin': 'delivery default 1.05; outline gives no numeric significance threshold',
            'requirements': {'fusionFaster': speedup >= minimum_speedup,
                             'resourceLimitsReduceFusionBenefit': constrained < speedup}}
