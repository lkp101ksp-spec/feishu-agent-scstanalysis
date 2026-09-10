# Phase 20 bio 镜像：scanpy 单细胞分析栈（spec 2026-09-01 phase20 §3.1）
# 构建：docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile
# GPU 预留：后续 rapids-singlecell 栈另建 bio:gpu-latest tag，settings.bio_image 可切
FROM python:3.12-slim

# 清华源装单细胞栈（项目惯例：pip 默认走清华源）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph

# Phase 31 富集分析：gseapy + Enrichr 基因集预取
# （构建期有网时下载 hallmark/GO BP/KEGG 三库存 /opt/gene_sets/，
#   运行期容器 --network none 离线可用；源不可达时 build 报错重试即可）
COPY fetch_gene_sets.py /tmp/fetch_gene_sets.py
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple gseapy \
    && python /tmp/fetch_gene_sets.py \
    && rm /tmp/fetch_gene_sets.py

# Phase 33 批次整合：bbknn（独立层，保上方 gene_sets 缓存层）。
# annoy（bbknn 硬依赖）无 manylinux 轮需源码编译——同层临时装 g++，
# 编译完成后 purge，层体积近似不变。
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple bbknn \
    && apt-get purge -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

# Phase 34 B 类分析：liana（细胞通讯，内置 consensus/mouseconsensus
# 资源库随包分发，容器断网可用）。statsmodels/sklearn 已由 scanpy
# 传递依赖带入；liana 依赖 plotnine/kneed 等均为纯 Python/manylinux 轮，
# 无编译需求（若构建报编译错误，按 bbknn 层先例同层临时装 g++）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple liana

# Phase 35 自动注释：celltypist + 模型构建期预取（运行期断网可用）。
# 模型下载自 celltypist.cog.sanger.ac.uk；不可达时 build 报错重试即可。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple celltypist \
    && python -c "from celltypist import models; \
        models.download_models(model=['Immune_All_Low.pkl', 'Immune_All_High.pkl'])" \
    && mkdir -p /opt/celltypist_models \
    && cp /root/.celltypist/data/models/*.pkl /opt/celltypist_models/

# Phase 35 双联体：scrublet（依赖 annoy 无 manylinux 轮需源码编译——
# 同层临时装 g++，编译完成后 purge，bbknn 层先例）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple scrublet \
    && apt-get purge -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

# Phase 36 调控网络：pyscenic 0.12.1。锁 setuptools<81（84+ 删了
# pkg_resources，ctxcore import 即炸）；sitecustomize 补 numpy>=1.24
# 移除的别名（np.object/np.float），放 site-packages 由 site 自动导入
# 以覆盖 dask worker 子进程。宿主 probe 实测：pandas 2.3.3/numpy 2.5.2/
# dask 2026.8.0 下三幕全绿（GRNBoost2 走 create_graph 绕路、prune2df
# from_delayed 物化 monkeypatch——均在 scenic.py 内）。
# 注：ctxcore 0.2.0（当前 PyPI 最新）增量 prefetch 缓存有 bug
# （difference 未排除已加载列 → append_column 重名 → select KeyError），
# 0.1.1 sdist 在 Py3.12 下拉老 numpy 源码编译失败——不修版本，
# 在 scenic.py _prune 内 monkeypatch 禁用增量缓存（每次全量重读列）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pyscenic "setuptools<81"
COPY scenic_site/sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py

# Phase 37 多组学：muon WNN（探针实测 muon 0.1.9+mudata 0.4.1 与
# pandas 2.3.3/numpy 2.5.2 兼容；n_multineighbors<n_obs 防御在脚本内）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple muon

# Phase 37 虚拟敲除：R 4.5 + CRAN scTenifoldKnk 1.1（保真路线——算法链
# 封装在 R 包内，Python 端口维护停滞。P3M trixie 二进制优先（设 UA），
# 依赖链含需编译包（igraph 等）→ r-base-dev/g++ 同层装完即 purge。
# 探针实测：基底 trixie、apt r-base-core=4.5.0、P3M trixie 源 200）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends r-base-core r-base-dev g++ \
    && Rscript -e "options(HTTPUserAgent=sprintf('R/%s R (%s)', getRversion(), paste(getRversion(), R.version['platform'], R.version['arch'], R.version['os']))); install.packages('scTenifoldKnk', repos='https://packagemanager.posit.co/cran/__linux__/trixie/latest')" \
    && Rscript -e "library(scTenifoldKnk); cat('scTenifoldKnk', as.character(packageVersion('scTenifoldKnk')), 'ok\n')" \
    && apt-get purge -y --no-install-recommends r-base-dev g++ \
    && rm -rf /var/lib/apt/lists/*

# B1 CNV 推断：infercnvpy（成熟默认）+ cnvturbo（对齐 R inferCNV HMM i6）
# 双后端。纯 CPU 工具；若装包报原生编译缺失，按 bbknn 层先例同层临时
# 装 g++ 编译后 purge。两层分离：改坐标脚本不重装 pip。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple infercnvpy==0.6.1 cnvturbo==0.3.0 \
    && python -c "import infercnvpy, cnvturbo; print('cnv deps ok')"
COPY fetch_gene_pos.py /tmp/fetch_gene_pos.py
RUN python /tmp/fetch_gene_pos.py && rm /tmp/fetch_gene_pos.py

# 非 root 用户 + 可写目录（与 kernel 镜像惯例一致）
RUN useradd -u 1000 -m bio \
    && mkdir -p /ws /data /tmp/mpl \
    && chown -R bio /ws /tmp/mpl

# matplotlib 无头模式 + 缓存目录指向可写 tmp
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /ws

# 固化参数化脚本（BioRunner 调 python /opt/sc_tools/<name>.py）
COPY sc_tools/ /opt/sc_tools/
COPY r_tools/ /opt/r_tools/

# 短命容器：跑完即退（--rm），无 CMD 保活需求
CMD ["python", "/opt/sc_tools/load.py"]
