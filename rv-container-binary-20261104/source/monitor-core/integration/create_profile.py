#!/usr/bin/env python3
"""Create an alternative ecosystem Helm chart; original release stays intact."""
import argparse
from pathlib import Path
import shutil

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform-source', type=Path, required=True,
                        help='riscv-platform-components source directory')
    parser.add_argument('--output', type=Path, required=True, help='new chart directory')
    args = parser.parse_args()
    source = args.platform_source.resolve() / 'platform/ecosystem/chart'
    output = args.output.resolve()
    if not (source/'Chart.yaml').is_file():
        raise SystemExit('ecosystem chart not found')
    if output.exists():
        raise SystemExit('output already exists; select a new directory')
    if source == output or source in output.parents:
        raise SystemExit('output must be outside the original chart')
    shutil.copytree(source, output)
    templates = output/'templates'
    chart = output/'Chart.yaml'
    metadata = chart.read_text(encoding='utf-8')
    metadata = metadata.replace('RISC-V platform Prometheus monitoring with command-line verification',
                                'RISC-V lightweight monitor core with command-line verification')
    metadata = metadata.replace('version: 0.3.0', 'version: 0.3.0-monitor-core.1')
    metadata = metadata.replace('appVersion: "0.3.0"', 'appVersion: "0.1.0"')
    chart.write_text(metadata, encoding='utf-8', newline='\n')
    # Same Service name/port and values key preserve the controller URL. This
    # chart is an alternative backend, not a second Prometheus deployment.
    workload = (templates/'workloads.yaml').read_text(encoding='utf-8')
    workload = workload.replace('command: ["/bin/prometheus"]', 'command: ["/bin/rv-monitor-core"]')
    old = 'args: ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus", "--storage.tsdb.retention.time={{ .Values.retention }}"]'
    new = 'args: ["--config.file=/etc/prometheus/monitor.json", "--web.listen-address=:9090"]'
    if old not in workload:
        raise SystemExit('unexpected upstream chart; refusing incomplete adaptation')
    workload = workload.replace(old,new)
    workload = workload.replace('name: prometheus\n', 'name: monitor-core\n')
    workload = workload.replace('set prometheusImage to a validated linux/riscv64 image',
                                'set prometheusImage to your rv-monitor-core linux/riscv64 image')
    # This variant is explicitly in-memory. Avoid unused data mounts and PVCs.
    workload = workload.replace('        - {name: data, mountPath: /prometheus}\n','')
    workload = workload[:workload.index('      - name: data\n')] + workload[workload.index('---\napiVersion: v1\nkind: Service\n'):]
    workload = workload[:workload.index('{{ if .Values.storage.enabled }}\n---')]
    (templates/'workloads.yaml').write_text(workload,encoding='utf-8',newline='\n')
    config = '''apiVersion: v1
kind: ConfigMap
metadata:
  name: rv-prometheus-config
data:
  monitor.json: |
    {
      "targets": {{ .Values.nodeTargets | toJson }},
      "scrapeInterval": "10s",
      "scrapeTimeout": "5s",
      "retention": "15m",
      "lookback": "45s",
      "maxSeries": 10000,
      "maxSamplesPerScrape": 1000,
      "maxBodyBytes": 2097152
    }
'''
    (templates/'config.yaml').write_text(config,encoding='utf-8',newline='\n')
    (output/'MONITOR-CORE-NOTES.txt').write_text(
        '轻量监控后端：仅内存15分钟历史，不提供原Prometheus的7天持久化、完整PromQL、网页和告警。\n'
        '设置prometheusImage为新镜像，nodeTargets沿用节点agent地址；控制器prometheusURL保持原值。\n'
        '本目录用于替换原ecosystem chart，不要并行安装两个同名Service。\n',encoding='utf-8')
    print(output)

if __name__ == '__main__':
    main()
