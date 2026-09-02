# Phase 25 bio GPU 镜像：rapids-singlecell 栈（spec 2026-09-02-bio-gpu-image-design §1.1）
# 构建：docker build -t feishu-research-agent/bio:gpu-latest sandbox -f sandbox/bio-gpu.Dockerfile
# 运行需 --gpus all（BioRunner gpus=True 透传）；CUDA 运行库由 nvidia-*-cu12 wheel 自带
FROM python:3.12-slim

# 依赖安装（降级路径）：rapids-singlecell >=0.15 官方只发 sdist（无 wheel，
# slim 镜像无 CUDA toolchain 无法编译），故钉 0.14.1（有官方 py3 wheel）。
# 0.14.1 的 GPU 依赖在 rapids12 extra：cupy-cuda12x + cuml/cugraph/cudf/cuvs-cu12。
# 注意 rsc 0.14.1 仅声明 cuml-cu12>=25.10 无上限，而 RAPIDS 26.x 已移除
# cuml.internals.input_utils（rsc 0.14.1 import 即崩），故 RAPIDS 全家桶加 <26 上限，
# 解析到 25.12 世代；dask-cuda/dask-cudf 由 cuml 依赖链自动跟随。
# pypi.org 国际链路实测仅 ~35 kB/s（libcudf 716MB 不可行），而清华镜像已同步
# 这些 nvidia/RAPIDS wheel 且国内 ~40MB/s，故全部走清华源
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph \
 && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    "rapids-singlecell[rapids12]==0.14.1" \
    "cuml-cu12<26" "cugraph-cu12<26" "cudf-cu12<26" "cuvs-cu12<26" "dask-cuda<26"

# 非 root 用户 + 可写目录（与 CPU 镜像一致）
RUN useradd -u 1000 -m bio \
    && mkdir -p /ws /data /tmp/mpl \
    && chown -R bio /ws /tmp/mpl

# matplotlib 无头模式 + 缓存目录指向可写 tmp
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /ws

# 固化参数化脚本（BioRunner 调 python /opt/sc_tools/<name>.py）
COPY sc_tools/ /opt/sc_tools/

# 短命容器：跑完即退（--rm），无 CMD 保活需求
CMD ["python", "/opt/sc_tools/load.py"]
