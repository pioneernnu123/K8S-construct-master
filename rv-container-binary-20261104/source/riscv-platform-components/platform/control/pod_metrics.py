"""Read real Pod memory usage for a Deployment's resource policy."""
from datetime import datetime

from common import metric_sample
from scheduler import requests


def has_controller(obj, kind, uid):
    return any(ref.get('controller') is True and ref.get('kind') == kind and ref.get('uid') == uid
               for ref in obj['metadata'].get('ownerReferences', []))


def epoch(timestamp):
    return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()


def pod_memory_sample(kube, deployment, now, max_age, read_samples):
    """Require a current, complete sample before changing the replica count."""
    metadata = deployment['metadata']
    namespace = metadata['namespace']
    replica_sets = {rs['metadata']['uid'] for rs in kube.items('replicasets', namespace)
                    if has_controller(rs, 'Deployment', metadata['uid'])}
    pods = [pod for pod in kube.items('pods', namespace)
            if not pod['metadata'].get('deletionTimestamp')
            and pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed')
            and any(has_controller(pod, 'ReplicaSet', uid) for uid in replica_sets)]
    replicas = deployment['spec']['replicas']
    if not replicas or len(pods) != replicas or any(
        not any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod.get('status', {}).get('conditions', []))
        for pod in pods
    ):
        raise ValueError('waiting for all Deployment replicas to be Ready before scaling')

    by_uid = {}
    for item in read_samples():
        uid = item['metric'].get('pod_uid')
        by_uid.setdefault(uid, []).append(item)
    samples = []
    for pod in pods:
        name = pod['metadata']['name']
        metrics = by_uid.get(pod['metadata']['uid'], [])
        if not metrics:
            raise ValueError('memory sample missing for ' + name)
        if len(metrics) != 1:
            raise ValueError('ambiguous memory sample for ' + name)
        timestamp = float(metrics[0]['value'][0])
        created = pod['metadata'].get('creationTimestamp')
        if now - timestamp > max_age or timestamp > now + 5 or (created and timestamp < epoch(created)):
            raise ValueError('memory sample outside the allowed time window for ' + name)
        used = metric_sample(metrics, now, max_age)
        requested = requests(pod).get('memory', 0)
        if requested < 1 or used < 0:
            raise ValueError('invalid Pod memory usage or request for ' + name)
        samples.append({'pod': name, 'uid': pod['metadata']['uid'], 'usedBytes': int(used),
                        'requestedBytes': int(requested), 'timestamp': timestamp})
    value = sum(row['usedBytes'] for row in samples) / sum(row['requestedBytes'] for row in samples)
    return value, min(row['timestamp'] for row in samples), samples
