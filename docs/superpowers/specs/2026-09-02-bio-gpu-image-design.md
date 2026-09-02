# Phase 25 GPU 镜像 bio:gpu-latest 设计文档

日期：2026-09-02
状态：已确认（用户 2026-09-02 批准设计）

## 背景

Phase 20 交付了 CPU 单细胞栈（bio:cpu-latest，scanpy），spec §8 预留
`bio:gpu-latest` 排期。宿主实测：RTX 3090 24GB（驱动 591.74 / CUDA 13.1），
Docker Desktop（WSL2 后端）容器内 GPU 透传验证通过
（`docker run --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` 正常）。

用户 skills 库 gpu-singlecell-analysis 已在 WSL2 miniconda 实战过
rapids-singlecell 全流程，其踩坑记录（rmm 勿手动配置、HVG 用 seurat flavor、
rank_genes_groups 用 use_raw=False）直接作为本设计实现约束。

触发时机（spec 原计划"等真实大队列数据"）由用户主动提前：有 N 卡即做。

## 决策记录（头脑风暴确认）

- **范围**：仅 sc_* 单细胞栈（sc_process 重步骤 GPU 化）。st_* 不动——
  避免 torch CUDA 膨胀（Phase 22 刚砍掉的 7GB）复发。
- **脚本策略**：一套 sc_tools 脚本双栈自适应（import 探测），
  两镜像 COPY 同一份脚本，零分叉。
- **开关**：settings.bio_use_gpu 默认 False，GPU 为显式 opt-in。

## 1. 架构与组件

### 1.1 新镜像 sandbox/bio-gpu.Dockerfile → bio:gpu-latest

沿用 bio.Dockerfile 骨架：python:3.12-slim + 清华源 + bio 用户（uid 1000）
+ /ws /data /tmp/mpl + COPY 同一套 sc_tools。栈层替换/追加：

```dockerfile
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph \
    rapids-singlecell
```

rapids-singlecell 的 pip 依赖链自动带入 cupy-cuda12x / cuml-cu12 /
cugraph-cu12 / pylibraft-cu12 等，CUDA 运行库由 nvidia-*-cu12 wheel 自带，
**不需要 nvidia/cuda base 镜像**。构建后实测若报缺库（libcudart 等），
再退 nvidia/cuda:12.x-runtime base 重建（计划内含此分支）。

镜像体积预期：CPU 镜像 2.4GB + RAPIDS 栈 ~2-3GB → 4.5-5.5GB 量级。

### 1.2 sc_tools/process.py 双栈自适应

开头探测：

```python
try:
    import rapids_singlecell as rsc
    _GPU = True
except ImportError:
    _GPU = False
```

GPU 分支替换的重步骤（其余步骤两路径共用 scanpy）：

| 步骤 | CPU 路径 | GPU 路径 |
|---|---|---|
| PCA | sc.tl.pca | rsc.pp.pca |
| neighbors | sc.pp.neighbors | rsc.pp.neighbors |
| UMAP | sc.tl.umap | rsc.tl.umap |
| leiden | sc.tl.leiden | rsc.tl.leiden |
| rank_genes_groups | sc.tl.rank_genes_groups | rsc.tl.rank_genes_groups(method="wilcoxon", use_raw=False) |

实现约束（来自 skill 实战记录）：不手动配置 rmm；HVG 用 flavor="seurat"；
scale 不用 max_value。

stdout 增加一行 `accelerator: gpu / cpu`；产物（processed.h5ad /
result.json / figures）schema 与 CPU 路径完全一致——下游 sc_markers /
sc_plot / write_doc 无感。

sc_load / sc_qc / sc_plot 不动（I/O 与绘图无 GPU 收益）。

### 1.3 BioRunner --gpus 透传（基建补缺）

```python
def run(self, ..., gpus: bool = False) -> BioRunResult:
    args = self._docker_args(image, mounts, timeout, network, workdir, gpus=gpus)
```

_docker_args 在 `"--rm"` 之后插 `"--gpus", "all"`（仅 gpus=True 时）。
现有所有调用方不传该参数，行为不变。

### 1.4 settings 两配置

```python
bio_use_gpu: bool = False        # env BIO_USE_GPU
bio_gpu_image: str = "feishu-research-agent/bio:gpu-latest"  # env BIO_GPU_IMAGE
```

### 1.5 l3_singlecell.py sc_process handler

注册闭包拿 bio_use_gpu / bio_gpu_image（沿用 st_deconvolve 的闭包注入
惯例）；执行时：

```python
if bio_use_gpu:
    image, gpus = bio_gpu_image, True
else:
    image, gpus = bio_image, False
runner.run(image=image, ..., gpus=gpus)
```

## 2. 数据流

sc_process 调用 → handler 按 bio_use_gpu 选镜像+gpus → BioRunner
`docker run --rm --gpus all ...` → process.py 探测到 rsc 走 GPU 分支 →
产物写数据集目录（schema 不变）→ 结果返回。

## 3. 错误处理

- GPU 路径 CUDA 错误（OOM 等）：不静默回退 CPU，直接失败报错（显性优于
  隐性；3090 24GB 官方口径够 ~50-100k 细胞，超限应让用户知道）
- gpus=True 但宿主无 GPU / 透传失效：docker 自身报错原样上抛
- bio_use_gpu=True 但 bio:gpu-latest 未构建：docker "image not found"
  原样上抛（报错信息自解释）

## 4. 测试策略

### 4.1 单测（TDD）

- test_bio_runner.py 追加：gpus=True → _docker_args 含 "--gpus","all"；
  默认不含
- test_l3_singlecell.py 追加：bio_use_gpu=True 时 handler 以 GPU 镜像 +
  gpus=True 调 BioRunner（mock 断言）；False 时现状不变
- settings：BIO_USE_GPU / BIO_GPU_IMAGE env 覆盖

### 4.2 镜像级验证

- 构建成功 + import 探测（容器内 rsc/cupy import + cuda.is_available）
- tiny_scrna 跑 GPU 镜像 process.py：accelerator: gpu、3 簇、
  markers 与 CPU 结果对照（簇数一致、各簇 top markers 集合重合）
- 冒烟 sc 5 步链在 BIO_USE_GPU=true 下全程 PASS

### 4.3 回归

默认（bio_use_gpu=false）下全量回归 840+ 不变。

## 5. 验收标准

1. bio:gpu-latest 构建成功，体积 < 6GB
2. tiny_scrna GPU 路径端到端通过，结果与 CPU 路径一致（簇数 + top markers）
3. stdout 可见 accelerator: gpu
4. 默认开关关闭时全量回归与冒烟行为零变化

## 6. 非目标（YAGNI）

- st_* / cell2location GPU 化（CUDA 膨胀复发风险，下轮再说）
- 大数据量 benchmark（无真实大队列数据在手；tiny 数据 GPU 反而慢属正常）
- 多 GPU / 显卡选择（单机单卡）
- CPU/GPU 结果逐比特一致性（浮点实现不同，只要求统计层面一致：
  簇数、top markers 集合）
