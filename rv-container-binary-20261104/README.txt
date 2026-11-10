课题三容器管理第三方测试交付包
版本 20261104，目标平台 Linux/riscv64

对应《结题测试大纲-1009》课题三容器化解决方案的 3-2-0001 至 3-2-0013。按本说明安装项目程序和镜像，再按 TEST-STEPS.txt 逐项执行测试。COVERAGE.txt 用于按大纲编号查找入口，VALIDATION.json 记录交付包的验证状态。

一、测试环境

使用不少于两台 RISC-V 服务器，至少一个 Kubernetes 控制节点、一个可调度工作节点。部署环境为 openEuler 24.03 LTS、Kubernetes v1.29.1、containerd 和 cgroup v1。现场集群需具备 CNI、kubectl 和提供 kubeflow.org/v2beta1 的 MPI Operator。安装脚本使用 Bash、Python 3、systemd 和 Helm。包内程序及镜像已完成构建，可离线部署。

工作节点的项目采集程序从 Pod cgroup 读取内存用量，通过包内监控提供查询。项目控制器根据 ResourcePolicy 执行扩缩容，并为指定 rv-scheduler 的 Pod 执行选节点和绑定。升级已部署的环境时，在工作节点重新执行 node-agent 安装，在控制节点重新执行 controller 安装。

二、校验并生成配置

在解压出的发布目录执行：

    sha256sum -c SHA256SUMS
    python3 -B tools/release.py verify

在控制节点使用有管理权限的 kubeconfig。下面参数需要换成现场值，worker 参数可以重复指定。

    export KUBECONFIG=/实际路径/kubeconfig
    export RV_SRC="$PWD/source/riscv-platform-components"
    python3 -B "$RV_SRC/platform/configure.py" --master-ip 192.0.2.10 \
      --worker worker1,192.0.2.11,root --kubeconfig "$KUBECONFIG" \
      --output-dir "$PWD/site-config"
    export RV_ENV="$PWD/site-config/rv-platform.env"
    source "$RV_ENV"

如果节点名称和 kubernetes.io/hostname 标签不同，在 site-config/rv-monitoring.json 的 nodeSelector 中填实际标签。现场配置、kubeconfig 与测试输出应保存在包的原始交付文件之外或独立目录。

三、按节点角色安装

在每个会调度测试 Pod 的节点，从同一包的发布目录运行以下命令。运行时需要足够磁盘空间以解压 MPI 镜像。

    sudo python3 -B tools/install.py images
    sudo python3 -B tools/install.py node-agent
    sudo ctr -n k8s.io images ls

确认三个镜像均已导入，并检查采集服务状态。

    systemctl is-active rv-node-exporter

节点采集服务使用 TCP 9108，监控 Pod 需能访问该端口。Pod 内存采集支持 Kubernetes 默认 kubepods 层级的 systemd 和 cgroupfs 布局。

在控制节点执行：

    sudo --preserve-env=KUBECONFIG python3 -B tools/install.py controller \
      --config "$PWD/site-config/rv-platform.json"
    command -v helm || sudo python3 -B tools/install.py helm
    python3 -B tools/install.py monitoring --config "$PWD/site-config/rv-monitoring.json"
    python3 -B tools/release.py preflight
    python3 -B "$RV_SRC/platform/ecosystem/verify.py" \
      --prometheus "$PROMETHEUS_URL" --target "$WORKER_IP:9108"

确认安装检查输出 passed: true，再开始测试。

四、运行正式用例

在控制节点按 TEST-STEPS.txt 的 3-2-0001 至 3-2-0013 顺序逐项执行。每项包括执行命令、操作过程、观察位置和通过标准。第 5 项使用独占的测试工作节点。第 13 项汇总同一交付包的前 12 项报告并核验公开提交。
