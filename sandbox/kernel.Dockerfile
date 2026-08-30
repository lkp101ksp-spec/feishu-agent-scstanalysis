# Phase 13 T2 kernel 镜像：python:3.11-slim + 数据科学基础包
# 构建：docker build -t feishu-research-agent/kernel:latest sandbox -f sandbox/kernel.Dockerfile
FROM python:3.11-slim

# 清华源装数据科学基础包（项目惯例：pip 默认走清华源）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas matplotlib

# 非 root 用户（docker run -u 1000:1000 配套）+ 可写目录
RUN useradd -u 1000 -m kernel \
    && mkdir -p /workspace /tmp/mpl \
    && chown -R kernel /workspace /tmp/mpl

# matplotlib 缓解只读文件系统：缓存目录指向 tmpfs
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /workspace

# 执行 harness 固化进镜像（KernelPool.exec_code 调 python /opt/run_user.py <file>）
COPY run_user.py /opt/run_user.py

# 常驻等待 exec（docker run 无 CMD override，靠默认命令保活容器）
CMD ["sleep", "infinity"]
