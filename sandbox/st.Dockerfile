# Phase 21 st 镜像：空间转录组分析栈（spec 2026-09-01 phase21 §2.1）
# 构建：docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile
# 批②追加 commot/cell2location 时只改本文件的 requirements 层（分层缓存友好）
FROM python:3.12-slim

# 清华源装空间转录组栈（项目惯例：pip 默认走清华源）
# commot 0.0.3 用 np.Inf（NumPy 2 已移除，包内仅 _usot.py 一处）→ 装后 sed 修补
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph squidpy commot \
    && sed -i 's/np\.Inf\b/np.inf/g' \
        /usr/local/lib/python3.12/site-packages/commot/_optimal_transport/_usot.py

# Phase 22 瘦身：torch 固定 CPU wheel（镜像 7.17GB→~2.5GB，免掉 nvidia-* CUDA 依赖）
# 注：原计划用清华 tuna pytorch-wheels（https://mirrors.tuna.tsinghua.edu.cn/
# pytorch-wheels/cpu/），2026-09-02 两次实测整站 404 已下线；改用上海交大 SJTUG
# 同构镜像（上游同为 download.pytorch.org/whl，PEP503 结构，torch/ 项目页
# 含 .whl 直链可作 find-links）。2.14.0 为 2026-09-02 查得的 cp312 x86_64 最新
# +cpu 版（manylinux_2_28_x86_64；约束核对：cell2location 0.1.5 要 torch>=1.9.0、
# lightning 要 torch<4.0,>=2.1.0、scvi-tools 1.5 无上界，均兼容）。失败即构建
# 失败——暴露问题优于静默 5GB 膨胀（原"回退普通 torch"分支已删）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -f https://mirrors.sjtug.sjtu.edu.cn/pytorch-wheels/cpu/torch/ \
    "torch==2.14.0+cpu"

# 批③：cell2location（scvi-tools/pyro 栈）；numpy2 已移除 np.Inf，包内引用用
# find 递归 sed 修补（sh 无 globstar，** glob 不可靠），python import 验证兜底
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple cell2location \
    && find /usr/local/lib/python3.12/site-packages/cell2location -name '*.py' \
        -exec sed -i 's/np\.Inf\b/np.inf/g' {} +; \
    python -c "import cell2location; print('cell2location', cell2location.__version__)"

# Phase 46 st_cnv：infercnvpy + GRCh38 坐标 TSV（与 bio.Dockerfile B1 层
# 同构；两层分离——改坐标脚本不重装 pip；fetch 需构建期网络）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple infercnvpy==0.6.1 \
    && python -c "import infercnvpy; print('infercnvpy', infercnvpy.__version__)"
COPY fetch_gene_pos.py /tmp/fetch_gene_pos.py
RUN python /tmp/fetch_gene_pos.py && rm /tmp/fetch_gene_pos.py

# 非 root 用户 + 可写目录（与 bio:cpu 镜像惯例一致）
RUN useradd -u 1000 -m bio \
    && mkdir -p /ws /data /tmp/mpl \
    && chown -R bio /ws /tmp/mpl

# matplotlib 无头模式 + 缓存目录指向可写 tmp
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /ws

# 固化参数化脚本（BioRunner 调 python /opt/st_tools/<name>.py）
COPY st_tools/ /opt/st_tools/

# 短命容器：跑完即退（--rm），无 CMD 保活需求
CMD ["python", "/opt/st_tools/load.py"]
