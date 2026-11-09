"""Drive the project policy and require its own scale action as evidence."""


def start(t):
    t.apply('deployment.yaml')
    t.rollout('load')
    t.apply('resource-policy.yaml')
    assert not t.items('horizontalpodautoscalers'), 'unexpected competing autoscaler'

    def baseline():
        policy = t.get('resourcepolicy', 'load')
        status = policy.get('status', {})
        if status.get('phase') == 'Ready' and status.get('metricSource') == 'PodMemory':
            return policy
    t.record('autoscaler-baseline', policy=t.wait(baseline, '项目控制器已读取初始 Pod 内存样本'))
    pod = t.items('pods')[0]['metadata']['name']
    t.cmd('exec', '-n', t.ns, pod, '--', '/bin/rv-test-workload', 'marker', 'on')


def verify_scale(t, expanding):
    def completed():
        deployment = t.get('deployment', 'load')
        policy = t.get('resourcepolicy', 'load')
        status = policy.get('status', {})
        action = status.get('lastAction') or {}
        replicas = deployment['spec']['replicas']
        if (status.get('metricSource') != 'PodMemory' or not status.get('podMemorySamples')
                or action.get('type') != 'scale' or action.get('to') != replicas
                or deployment.get('status', {}).get('readyReplicas', 0) != replicas):
            return None
        before = action['from']
        if (expanding and replicas > before) or (not expanding and replicas == 1 < before):
            return deployment, policy

    label = '扩容且副本就绪' if expanding else '缩容至一个就绪副本'
    deployment, policy = t.wait(completed, '项目控制器完成' + label)
    assert not t.items('horizontalpodautoscalers'), 'unexpected competing autoscaler'
    t.record('expanded' if expanding else 'shrunk', deployment=deployment, policy=policy)
