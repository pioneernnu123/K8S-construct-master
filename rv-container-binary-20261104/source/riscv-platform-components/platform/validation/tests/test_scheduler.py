"""Drive the six shipped acceptance manifests through the scheduler API paths."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'control'), str(ROOT/'acceptance'), str(ROOT/'node')]
from scheduler import BOUND, NAME, VICTIMS, Scheduler
from support import render
from agent import pod_memory


def load(directory, filename):
    source = ROOT/'acceptance'/directory/'manifests'/filename
    pod = json.loads(render(source.read_text(encoding='utf-8'), {
        'NS':'test', 'NODE_HOSTNAME':'worker', 'TEST_NODE':'worker', 'NONCE':'abcd',
        'WORKLOAD_IMAGE':'example.invalid/workload', 'LOW_CPU':'600m', 'HIGH_CPU':'750m'}))
    name = pod['metadata']['name']
    pod['metadata'].update(uid='uid-'+name, resourceVersion='1')
    return pod


class Api:
    def __init__(self):
        self.pods = {}
        self.actions = []
        self.node = {'metadata':{'name':'worker','labels':{'kubernetes.io/hostname':'worker'}}, 'spec':{},
                     'status':{'conditions':[{'type':'Ready','status':'True'}],
                               'allocatable':{'cpu':'1','memory':'1Gi','pods':'16'}}}

    def items(self, kind):
        return copy.deepcopy([self.node] if kind == 'nodes' else list(self.pods.values()))

    def add(self, directory, filename):
        pod = load(directory,filename)
        self.pods[pod['metadata']['name']] = pod
        return pod

    def call(self, *args, data=None):
        if args[:2] == ('patch','pod'):
            self.actions.append('status')
            patch = json.loads(args[args.index('-p')+1])
            self.pods[args[2]]['status'] = patch[-1]['value']
        else:
            subresource = args[2].split('/')[-1]
            self.actions.append(subresource)
            pod = self.pods[data['metadata']['name']]
            if subresource == 'binding':
                assert data['metadata']['uid'] == pod['metadata']['uid']
                pod['spec']['nodeName'] = data['target']['name']
                pod['metadata'].setdefault('annotations',{}).update(data['metadata']['annotations'])
            elif subresource == 'eviction':
                assert data['deleteOptions']['preconditions']['uid'] == pod['metadata']['uid']
                pod['metadata']['deletionTimestamp'] = 'terminating'
            else:
                raise AssertionError(args)
        return {}

    def patch(self, kind, pod, fields):
        self.pods[pod['metadata']['name']].update(copy.deepcopy(fields))
        return copy.deepcopy(self.pods[pod['metadata']['name']])


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.api = Api()
        self.scheduler = Scheduler(self.api)

    def check_bound(self, pod):
        self.assertEqual(pod['spec']['schedulerName'], NAME)
        self.assertEqual(pod['spec']['nodeName'], 'worker')
        self.assertEqual(pod['metadata']['annotations'][BOUND], NAME)

    def check_rejected(self, pod, reason):
        self.assertFalse(pod['spec'].get('nodeName'))
        self.assertEqual(pod['status']['conditions'][0]['message'], 'rv-scheduler: '+json.dumps({'worker':reason}))

    def test_affinity_uses_actual_acceptance_rules(self):
        anchor = self.api.add('03-pod-affinity','anchor.yaml')
        self.scheduler.step(); self.check_bound(anchor)
        follower = self.api.add('03-pod-affinity','follower.yaml')
        unmatched = self.api.add('03-pod-affinity','unmatched.yaml')
        self.scheduler.step(); self.check_bound(follower); self.check_rejected(unmatched,'pod-affinity')

    def test_anti_affinity_and_node_exclusion(self):
        anchor = self.api.add('04-node-exclusion','anchor.yaml')
        self.scheduler.step(); self.check_bound(anchor)
        anti = self.api.add('04-node-exclusion','anti.yaml')
        excluded = self.api.add('04-node-exclusion','pod.yaml')
        self.scheduler.step()
        self.check_rejected(anti,'pod-anti-affinity'); self.check_rejected(excluded,'node-affinity')

    def test_taint_reject_tolerate_and_node_affinity(self):
        self.api.node['spec']['taints'] = [{'key':'acceptance.riscv.io/taint-abcd','value':'yes','effect':'NoSchedule'}]
        rejected = self.api.add('06-taint-reject','pod.yaml')
        self.scheduler.step(); self.check_rejected(rejected,'untolerated-taint')
        tolerated = self.api.add('07-taint-tolerate','pod.yaml')
        self.scheduler.step(); self.check_bound(tolerated)
        self.api.node['spec']['taints'] = []
        target = self.api.add('12-node-affinity','pod.yaml')
        self.scheduler.step(); self.check_bound(target)

    def test_preemption_waits_until_eviction_frees_resources(self):
        low = self.api.add('05-preemption','low.yaml'); low['spec']['priority'] = -1000
        self.scheduler.step(); self.check_bound(low)
        high = self.api.add('05-preemption','high.yaml'); high['spec']['priority'] = -900
        self.scheduler.step()
        self.assertIn('eviction', self.api.actions)
        self.assertFalse(high['spec'].get('nodeName'))
        self.scheduler.step()
        self.assertEqual(self.api.actions.count('eviction'), 1)
        del self.api.pods['low']
        self.scheduler.step(); self.check_bound(high)
        self.assertEqual(json.loads(high['metadata']['annotations'][VICTIMS]), ['uid-low'])

    def test_scheduler_does_not_claim_or_preempt_default_workloads(self):
        low = self.api.add('05-preemption','low.yaml')
        low['spec'].update(schedulerName='default-scheduler', priority=-1000)
        self.scheduler.step(); self.assertEqual(self.api.actions, [])
        low['spec']['nodeName'] = 'worker'
        high = self.api.add('05-preemption','high.yaml'); high['spec']['priority'] = -900
        self.scheduler.step(); self.check_rejected(high,'insufficient-resources')
        self.assertNotIn('eviction',self.api.actions)

    def test_pod_cgroup_v1_systemd_and_v2_cgroupfs(self):
        uid = 'b67930df-53fd-4609-a7b1-055a1f8bf9a9'
        for v2 in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if v2:
                    (root/'cgroup.controllers').touch()
                    pod = root/'kubepods'/'burstable'/('pod'+uid)
                    usage, inactive = 'memory.current','inactive_file'
                else:
                    pod = root/'memory'/'kubepods.slice'/'kubepods-burstable.slice'/('kubepods-burstable-pod'+uid.replace('-','_')+'.slice')
                    usage, inactive = 'memory.usage_in_bytes','total_inactive_file'
                pod.mkdir(parents=True)
                (pod/usage).write_text('1500\n')
                (pod/'memory.stat').write_text(inactive+' 200\n')
                self.assertEqual(pod_memory(root),(True,{uid:1300}))
