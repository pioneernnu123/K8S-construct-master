"""Project scheduler for explicitly opted-in Kubernetes Pods."""
import json
import sys

from common import PREFIX, now, quantity

NAME = 'rv-scheduler'
BOUND = PREFIX + 'scheduled-by'
VICTIMS = PREFIX + 'preempted-uids'
PREEMPTOR = PREFIX + 'allow-preemption'
PREEMPTIBLE = PREFIX + 'preemptible'
REQUIRED = 'requiredDuringSchedulingIgnoredDuringExecution'


def matches_requirement(values, rule):
    key, operator = rule['key'], rule['operator']
    present = key in values
    value = values.get(key)
    choices = rule.get('values', [])
    if operator == 'In':
        return present and value in choices
    if operator == 'NotIn':
        return not present or value not in choices
    if operator in ('Exists', 'DoesNotExist'):
        return present == (operator == 'Exists')
    if operator in ('Gt', 'Lt'):
        try:
            return present and len(choices) == 1 and (
                int(value) > int(choices[0]) if operator == 'Gt' else int(value) < int(choices[0]))
        except (ValueError, TypeError):
            return False
    raise ValueError('unsupported selector operator: ' + operator)


def labels_match(labels, selector):
    return (selector is not None
            and all(labels.get(k) == v for k, v in selector.get('matchLabels', {}).items())
            and all(matches_requirement(labels, rule) for rule in selector.get('matchExpressions', [])))


def node_affinity(spec, node):
    labels = node['metadata'].get('labels', {})
    if not labels_match(labels, {'matchLabels': spec.get('nodeSelector', {})}):
        return False
    affinity = spec.get('affinity', {}).get('nodeAffinity', {})
    if REQUIRED not in affinity:
        return True
    fields = {'metadata.name': node['metadata']['name']}
    for term in affinity[REQUIRED].get('nodeSelectorTerms', []):
        expressions = term.get('matchExpressions', [])
        matches = term.get('matchFields', [])
        if ((expressions or matches) and all(matches_requirement(labels, r) for r in expressions)
                and all(r['key'] in fields and matches_requirement(fields, r) for r in matches)):
            return True
    return False


def tolerated(taint, tolerations):
    for item in tolerations:
        if item.get('effect') and item['effect'] != taint['effect']:
            continue
        op = item.get('operator', 'Equal')
        key = item.get('key', '')
        if op == 'Exists' and (not key or key == taint['key']):
            return True
        if op == 'Equal' and key == taint['key'] and item.get('value', '') == taint.get('value', ''):
            return True
    return False


def requests(pod):
    spec = pod['spec']
    def add(target, values):
        for resource, value in values.items():
            target[resource] = target.get(resource, 0) + quantity(value)
    steady, sidecars, init_peak = {}, {}, {}
    for container in spec.get('initContainers', []):
        values = container.get('resources', {}).get('requests', {})
        if container.get('restartPolicy') == 'Always':
            add(sidecars, values)
            total = sidecars
        else:
            total = dict(sidecars)
            add(total, values)
        for resource, value in total.items():
            init_peak[resource] = max(init_peak.get(resource, 0), value)
    steady.update(sidecars)
    for container in spec.get('containers', []):
        add(steady, container.get('resources', {}).get('requests', {}))
    for resource, value in init_peak.items():
        steady[resource] = max(steady.get(resource, 0), value)
    add(steady, spec.get('overhead', {}))
    steady['pods'] = 1
    return steady


def active(pod):
    return pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed')


def priority(pod):
    return pod['spec'].get('priority') or 0


def fits(pod, node, assigned):
    usage = requests(pod)
    for peer in assigned:
        if peer['spec'].get('nodeName') == node['metadata']['name']:
            for resource, value in requests(peer).items():
                usage[resource] = usage.get(resource, 0) + value
    capacity = node.get('status', {}).get('allocatable', {})
    return all(value <= quantity(capacity.get(resource, '0')) for resource, value in usage.items())


def term_matches(term, owner, peer):
    if term.get('namespaceSelector') is not None or any(term.get(k) for k in ('matchLabelKeys', 'mismatchLabelKeys')):
        raise ValueError('namespaceSelector and dynamic label keys are outside scheduler scope')
    namespaces = term.get('namespaces') or [owner['metadata']['namespace']]
    return peer['metadata']['namespace'] in namespaces and labels_match(
        peer['metadata'].get('labels', {}), term.get('labelSelector'))


def topology_matches(term, first, second, nodes):
    key = term['topologyKey']
    first_labels = nodes[first]['metadata'].get('labels', {})
    second_labels = nodes.get(second, {}).get('metadata', {}).get('labels', {})
    return key in first_labels and key in second_labels and first_labels[key] == second_labels[key]


def pod_affinity(pod, node, assigned, nodes):
    name = node['metadata']['name']
    labels = node['metadata'].get('labels', {})
    rules = pod['spec'].get('affinity', {})
    for term in rules.get('podAffinity', {}).get(REQUIRED, []):
        peers = [peer for peer in assigned if term_matches(term, pod, peer)]
        if not peers and term_matches(term, pod, pod) and term['topologyKey'] in labels:
            continue  # The first Pod of a self-affine group can start the group.
        if not any(topology_matches(term, name, peer['spec']['nodeName'], nodes) for peer in peers):
            return 'pod-affinity'
    for term in rules.get('podAntiAffinity', {}).get(REQUIRED, []):
        if term['topologyKey'] not in labels or any(
            term_matches(term, pod, peer) and topology_matches(term, name, peer['spec']['nodeName'], nodes)
            for peer in assigned
        ):
            return 'pod-anti-affinity'
    for peer in assigned:
        for term in peer['spec'].get('affinity', {}).get('podAntiAffinity', {}).get(REQUIRED, []):
            if term_matches(term, peer, pod) and topology_matches(term, name, peer['spec']['nodeName'], nodes):
                return 'existing-pod-anti-affinity'
    return None


def filter_node(pod, node, assigned, nodes):
    spec = pod['spec']
    if (node['metadata'].get('deletionTimestamp') or node.get('spec', {}).get('unschedulable')
            or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status'].get('conditions', []))):
        return 'node-unavailable'
    if not node_affinity(spec, node):
        return 'node-affinity'
    if any(t['effect'] in ('NoSchedule', 'NoExecute') and not tolerated(t, spec.get('tolerations', []))
           for t in node.get('spec', {}).get('taints', [])):
        return 'untolerated-taint'
    return pod_affinity(pod, node, assigned, nodes)


def check_scope(pod):
    spec = pod['spec']
    if (spec.get('resourceClaims') or spec.get('topologySpreadConstraints') or spec.get('hostNetwork')
            or any('persistentVolumeClaim' in v or 'ephemeral' in v or 'csi' in v for v in spec.get('volumes', []))
            or any(port.get('hostPort') for c in spec.get('containers', []) + spec.get('initContainers', [])
                   for port in c.get('ports', []))):
        raise ValueError('Pod requires scheduling features outside the supported scope')


def plan(pod, nodes, assigned):
    check_scope(pod)
    reasons, options = {}, []
    annotations = pod['metadata'].get('annotations', {})
    can_preempt = annotations.get(PREEMPTOR) == 'true' and pod['spec'].get('preemptionPolicy') != 'Never'
    for name, node in sorted(nodes.items()):
        reason = filter_node(pod, node, assigned, nodes)
        if reason:
            reasons[name] = reason
            continue
        if fits(pod, node, assigned):
            soft = sum(t['effect'] == 'PreferNoSchedule' and not tolerated(t, pod['spec'].get('tolerations', []))
                       for t in node.get('spec', {}).get('taints', []))
            options.append((0, soft, name, []))
            continue
        reasons[name] = 'insufficient-resources'
        if not can_preempt:
            continue
        local = [peer for peer in assigned if peer['spec']['nodeName'] == name]
        if any(peer['metadata'].get('deletionTimestamp') for peer in local):
            continue  # Account for resources until the terminating Pods actually leave.
        candidates = [peer for peer in local if peer['metadata']['namespace'] == pod['metadata']['namespace']
                      and peer['spec'].get('schedulerName') == NAME and priority(peer) < priority(pod)
                      and peer['metadata'].get('annotations', {}).get(PREEMPTIBLE) == 'true']
        remaining = list(assigned)
        victims = []
        for peer in sorted(candidates, key=lambda x: (priority(x), x['metadata']['name'])):
            remaining.remove(peer)
            victims.append(peer)
            if not filter_node(pod, node, remaining, nodes) and fits(pod, node, remaining):
                options.append((len(victims), 0, name, victims))
                break
    if not options:
        return None, [], reasons
    _, _, name, victims = min(options, key=lambda option: option[:3])
    return name, victims, reasons


class Scheduler:
    def __init__(self, kube):
        self.kube = kube

    def pending(self, pod, reasons):
        metadata = pod['metadata']
        conditions = pod.get('status', {}).get('conditions', [])
        previous = next((c for c in conditions if c['type'] == 'PodScheduled'), {})
        message = NAME + ': ' + json.dumps(reasons, sort_keys=True)
        if previous.get('status') == 'False' and previous.get('message') == message:
            return
        condition = {'type': 'PodScheduled', 'status': 'False', 'reason': 'Unschedulable',
                     'message': message, 'lastTransitionTime': now()}
        updated = [c for c in conditions if c['type'] != 'PodScheduled'] + [condition]
        patch = [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': metadata['resourceVersion']},
                 {'op': 'add', 'path': '/status', 'value': {**pod.get('status', {}), 'conditions': updated}}]
        self.kube.call('patch', 'pod', metadata['name'], '-n', metadata['namespace'], '--subresource=status',
                       '--type=json', '-p', json.dumps(patch), '-o', 'json')

    def evict(self, pod, victims):
        for victim in victims:
            meta = victim['metadata']
            path = f"/api/v1/namespaces/{meta['namespace']}/pods/{meta['name']}/eviction"
            self.kube.call('create', '--raw', path, '-f', '-', data={
                'apiVersion': 'policy/v1', 'kind': 'Eviction',
                'metadata': {'name': meta['name'], 'namespace': meta['namespace']},
                'deleteOptions': {'preconditions': {'uid': meta['uid'], 'resourceVersion': meta['resourceVersion']}}})
            meta['deletionTimestamp'] = now()
            annotations = dict(pod['metadata'].get('annotations', {}))
            previous = json.loads(annotations.get(VICTIMS, '[]'))
            annotations[VICTIMS] = json.dumps(sorted(set(previous + [meta['uid']])))
            pod = self.kube.patch('pod', pod, {'metadata': {**pod['metadata'], 'annotations': annotations}})

    def bind(self, pod, node):
        meta = pod['metadata']
        path = f"/api/v1/namespaces/{meta['namespace']}/pods/{meta['name']}/binding"
        self.kube.call('create', '--raw', path, '-f', '-', data={
            'apiVersion': 'v1', 'kind': 'Binding',
            'metadata': {'name': meta['name'], 'namespace': meta['namespace'], 'uid': meta['uid'],
                         'resourceVersion': meta['resourceVersion'], 'annotations': {BOUND: NAME}},
            'target': {'apiVersion': 'v1', 'kind': 'Node', 'name': node}})

    def step(self):
        pods = self.kube.items('pods')
        pending = [pod for pod in pods if pod['spec'].get('schedulerName') == NAME and active(pod)
                   and not pod['spec'].get('nodeName') and not pod['spec'].get('schedulingGates')
                   and not pod['metadata'].get('deletionTimestamp')]
        if not pending:
            return
        nodes = {node['metadata']['name']: node for node in self.kube.items('nodes')}
        assigned = [pod for pod in pods if pod['spec'].get('nodeName') and active(pod)]
        for pod in sorted(pending, key=lambda x: (-priority(x), x['metadata'].get('creationTimestamp', ''))):
            try:
                try:
                    node, victims, reasons = plan(pod, nodes, assigned)
                except ValueError as error:
                    self.pending(pod, {'unsupported': str(error)})
                    continue
                if victims:
                    self.evict(pod, victims)
                elif node:
                    self.bind(pod, node)
                    assigned.append({**pod, 'spec': {**pod['spec'], 'nodeName': node}})
                else:
                    self.pending(pod, reasons)
            except Exception as error:
                print(now(), NAME, pod['metadata']['name'], error, file=sys.stderr, flush=True)
