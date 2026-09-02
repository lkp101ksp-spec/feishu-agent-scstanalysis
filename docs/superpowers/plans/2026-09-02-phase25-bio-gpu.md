# Phase 25 GPU 镜像 bio:gpu-latest 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 bio:gpu-latest（rapids-singlecell 栈），sc_process/sc_markers 重步骤 GPU 加速，BioRunner 补 --gpus 透传，settings 开关默认关闭。

**Architecture:** python:3.12-slim + pip rapids-singlecell（CUDA 运行库靠 nvidia-*-cu12 wheel 自带）；sc_tools 脚本 import 探测双栈自适应（CPU/GPU 镜像共用同一份脚本）；settings.bio_use_gpu 控制 handler 选镜像+gpus。

**Tech Stack:** Docker（--gpus all，已实测透传 RTX 3090）、rapids-singlecell（cupy-cuda12x/cuml-cu12/cugraph-cu12）、scanpy、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-bio-gpu-image-design.md`

**环境前提（已验证）：** RTX 3090 24GB，驱动 591.74/CUDA 13.1，`docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` 正常。

**Subagent 通用上下文（每个子代理任务描述都要带）：**
- 工作目录 `i:\飞书agent`，Windows PowerShell，python 用 `.\.venv\Scripts\python.exe`
- 项目规矩：代码文件加文件级 docstring + 函数级注释（中文）；不要主动写 git commit 之外的文档；严禁提交 .env
- 单测：`.venv\Scripts\python.exe -m pytest tests/unit/ -q`（当前基线 840 passed）
- Docker 相关命令耗时较长（构建/拉镜像），用 blocking=false + wait_ms_before_async=2000 发起后轮询
- 完成后报告：改动文件列表、新增/修改测试数、测试结果、docker 验证输出、commit hash

---

### Task 1: BioRunner --gpus 透传（TDD）

**Files:**
- Modify: `orchestrator/tools/bio/bio_runner.py`（run @79-100、_docker_args @158-183）
- Test: `tests/unit/test_bio_runner.py`（文件尾 Phase 23 区之后追加 Phase 25 区）

- [ ] **Step 1: 写失败测试**

`tests/unit/test_bio_runner.py` 文件尾追加：

```python
# === Phase 25：--gpus 透传 ===

def test_run_gpus_adds_flag(runner, fake_docker_ok):
    """gpus=True → docker cmd 的 --rm 后紧跟 --gpus all。"""
    runner.run("process", {"dataset_id": "abcdef123456"}, gpus=True)
    cmd = fake_docker_ok.cmd
    rm_idx = cmd.index("--rm")
    assert cmd[rm_idx + 1: rm_idx + 3] == ["--gpus", "all"]


def test_run_default_no_gpus(runner, fake_docker_ok):
    """默认 gpus=False → cmd 不含 --gpus。"""
    runner.run("qc", {"dataset_id": "abcdef123456"})
    assert "--gpus" not in fake_docker_ok.cmd
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py::test_run_gpus_adds_flag -q`
Expected: FAIL（TypeError: run() got an unexpected keyword argument 'gpus'）

- [ ] **Step 3: 实现**

`orchestrator/tools/bio/bio_runner.py` 三处修改：

run() 签名加参数并传入 _docker_args：

```python
    def run(
        self,
        name: str,
        args: dict,
        *,
        mounts: list[tuple[str, str]] | None = None,
        timeout_sec: int | None = None,
        image: str | None = None,
        script_dir: str | None = None,
        gpus: bool = False,
    ) -> dict:
        """短命容器执行 /opt/sc_tools/<name>.py，返回脚本 stdout 的 JSON。

        image/script_dir 可覆盖实例默认（st_* 工具用 st 镜像 + /opt/st_tools）；
        gpus=True 时 docker run 带 --gpus all（GPU 镜像，Phase 25）。
        """
        touch_last_access(self.workspace_root, args.get("dataset_id"))
        mnts = list(mounts) if mounts else []
        mnts.append((self.workspace_root, "/ws"))
        img = image if image is not None else self.image
        sdir = script_dir if script_dir is not None else "/opt/sc_tools"
        timeout = timeout_sec or self.timeout_sec
        workdir = "/ws"
        cmd = self._docker_args(
            img, mnts, timeout, self.network, workdir, gpus=gpus)
        cmd += ["python", f"{sdir}/{name}.py"]
```

_docker_args 加参数与插入逻辑（`"--rm"` 之后立即插）：

```python
    @classmethod
    def _docker_args(
        cls,
        image: str,
        mounts: list[tuple[str, str]],
        timeout: int,
        network: str,
        workdir: str,
        gpus: bool = False,
    ) -> list[str]:
        """docker run 完整参数（--rm + 挂载 + 资源限额 + network + GPU）。"""
        args = ["docker", "run", "--rm"]
        if gpus:
            args += ["--gpus", "all"]
        for host, cont in mounts:
            args += ["-v", f"{host}:{cont}"]
        args += [
            "--cpus", cls.cpus,
            "--memory", cls.memory,
            "--network", network,
            "-w", workdir,
            "-i",
            image,
        ]
        return args
```

- [ ] **Step 4: 跑测试确认通过 + bio_runner 全套回归**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py -q`
Expected: 35 passed（原 33 + 新 2；若数量不同以实际为准，全部通过即可）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/bio/bio_runner.py tests/unit/test_bio_runner.py
git commit -m "feat(phase25): BioRunner --gpus 透传（gpu 镜像基建）"
```

---

### Task 2: settings 开关 + sc_process/sc_markers GPU 分流（TDD）

**Files:**
- Modify: `config/settings.py`（bio 配置区尾部，st_image 行之后）
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`（register 签名 + sc_process/sc_markers handler）
- Modify: `orchestrator/app.py`（@129 register_l3_singlecell 调用）
- Test: `tests/unit/test_l3_singlecell.py`（文件尾追加）

- [ ] **Step 1: 写失败测试**

`tests/unit/test_l3_singlecell.py` 文件尾追加：

```python
# === Phase 25：GPU 镜像分流 ===

def _gpu_registry(tmp_path, bio_use_gpu):
    """带 GPU 开关的注册 fixture（本区用例专用）。"""
    runner = SimpleNamespace(
        run=MagicMock(return_value={
            "ok": True, "dataset_ref": "abc123", "n_cells": 100}),
        resolve_data_path=MagicMock(),
    )
    reg = ToolRegistry()
    register_l3_singlecell(reg, runner, bio_use_gpu=bio_use_gpu,
                           bio_gpu_image="bio:gpu-test")
    return reg, runner


def test_sc_process_uses_gpu_image_when_enabled(tmp_path):
    """bio_use_gpu=True → sc_process 以 GPU 镜像 + gpus=True 调 BioRunner。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=True)
    reg.get("sc_process").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw["image"] == "bio:gpu-test"
    assert kw["gpus"] is True


def test_sc_markers_uses_gpu_image_when_enabled(tmp_path):
    """bio_use_gpu=True → sc_markers 同样走 GPU 镜像。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=True)
    reg.get("sc_markers").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw["image"] == "bio:gpu-test"
    assert kw["gpus"] is True


def test_sc_process_default_cpu_when_disabled(tmp_path):
    """bio_use_gpu=False（默认）→ image=None（用 runner 默认）+ gpus=False。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=False)
    reg.get("sc_process").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw["image"] is None
    assert kw["gpus"] is False


def test_sc_qc_never_uses_gpu(tmp_path):
    """sc_qc 不受 GPU 开关影响（I/O 型步骤无 GPU 收益）。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=True)
    reg.get("sc_qc").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw.get("image") is None
    assert kw.get("gpus") is False
```

注意：`reg.get("sc_process")` 返回 ToolSpec，handler 属性直接可调（参照 tests/unit/test_param_coerce.py 的用法）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_singlecell.py -q`
Expected: 4 个新用例 FAIL（register_l3_singlecell 无 bio_use_gpu 参数）

- [ ] **Step 3: 实现**

`config/settings.py` bio 配置区尾部（`st_image` 行之后）追加：

```python
    # === Phase 25：GPU 加速（bio:gpu-latest，需宿主 N 卡 + docker --gpus） ===
    bio_use_gpu: bool = False       # env BIO_USE_GPU；True 时 sc_process/sc_markers 走 GPU 镜像
    bio_gpu_image: str = "feishu-research-agent/bio:gpu-latest"  # env BIO_GPU_IMAGE
```

`orchestrator/tools/builtin/l3_singlecell.py`：

register 签名：

```python
def register_l3_singlecell(
    registry: ToolRegistry,
    runner: BioRunner,
    *,
    bio_use_gpu: bool = False,
    bio_gpu_image: str = "feishu-research-agent/bio:gpu-latest",
) -> None:
    """注册 sc_* 5 工具（runner 由 runtime 装配后传入）。

    bio_use_gpu=True 时 sc_process/sc_markers 切 GPU 镜像 + --gpus all
    （Phase 25，spec 2026-09-02-bio-gpu-image-design §1.5）。
    """

    def _accel() -> tuple[str | None, bool]:
        """GPU 开关分流：开→(gpu_image, True)；关→(None=runner 默认镜像, False)。"""
        if bio_use_gpu:
            return bio_gpu_image, True
        return None, False
```

sc_process handler 改为：

```python
    def sc_process(*, dataset_ref: str, n_top_hvg: int = 2000,
                   n_pcs: int = 50, n_neighbors: int = 15,
                   resolution: float = 1.0) -> dict:
        """标准流程（归一化→HVG→PCA→UMAP→Leiden）→ processed.h5ad + umap.png。"""
        image, gpus = _accel()
        try:
            out = runner.run("process", {
                "dataset_id": dataset_ref,
                "n_top_hvg": n_top_hvg, "n_pcs": n_pcs,
                "n_neighbors": n_neighbors, "resolution": resolution,
            }, image=image, gpus=gpus)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

sc_markers handler 改为：

```python
    def sc_markers(*, dataset_ref: str, method: str = "wilcoxon",
                   top_n: int = 10) -> dict:
        """每簇差异基因 → markers JSON + dotplot.png。"""
        image, gpus = _accel()
        try:
            out = runner.run("markers", {
                "dataset_id": dataset_ref, "method": method,
                "top_n": top_n,
            }, image=image, gpus=gpus)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

sc_load / sc_qc / sc_plot 不动。

`orchestrator/app.py` @129 改为：

```python
                register_l3_singlecell(
                    self.registry, bio_runner,
                    bio_use_gpu=settings.bio_use_gpu,
                    bio_gpu_image=settings.bio_gpu_image,
                )
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_singlecell.py tests/unit/test_settings.py tests/unit/test_l1_l3_registry.py -q`
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add config/settings.py orchestrator/tools/builtin/l3_singlecell.py orchestrator/app.py tests/unit/test_l3_singlecell.py
git commit -m "feat(phase25): bio_use_gpu 开关 + sc_process/sc_markers GPU 镜像分流"
```

---

### Task 3: bio-gpu.Dockerfile 构建 + 容器探测

**Files:**
- Create: `sandbox/bio-gpu.Dockerfile`

- [ ] **Step 1: 写 Dockerfile**

```dockerfile
# Phase 25 bio GPU 镜像：rapids-singlecell 栈（spec 2026-09-02-bio-gpu-image-design §1.1）
# 构建：docker build -t feishu-research-agent/bio:gpu-latest sandbox -f sandbox/bio-gpu.Dockerfile
# 运行需 --gpus all（BioRunner gpus=True 透传）；CUDA 运行库由 nvidia-*-cu12 wheel 自带
FROM python:3.12-slim

# 清华源装单细胞栈 + RAPIDS（项目惯例：pip 默认走清华源）
# rapids-singlecell 依赖链带入 cupy-cuda12x/cuml-cu12/cugraph-cu12/pylibraft-cu12
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph \
    rapids-singlecell

# 非 root 用户 + 可写目录（与 CPU 镜像一致）
RUN useradd -u 1000 -m bio \
    && mkdir -p /ws /data /tmp/mpl \
    && chown -R bio /ws /tmp/mpl

ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /ws

COPY sc_tools/ /opt/sc_tools/

CMD ["python", "/opt/sc_tools/load.py"]
```

- [ ] **Step 2: 构建（耗时长，下载 RAPIDS wheel 数 GB）**

Run: `docker build -t feishu-research-agent/bio:gpu-latest sandbox -f sandbox/bio-gpu.Dockerfile`
Expected: 构建成功。

**降级路径**：若清华源缺 RAPIDS cu12 wheel（404/No matching distribution），把 pip 段改为双源——RAPIDS 系走官方 PyPI：

```dockerfile
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph \
 && pip install --no-cache-dir -i https://pypi.org/simple \
    rapids-singlecell
```

再不行退 nvidia/cuda:12.4.1-runtime-ubuntu22.04 base + apt python3.12……（先记录失败输出，找父代理商议，不要自行扩大改动）

- [ ] **Step 3: 容器内探测**

Run: `docker run --rm --gpus all feishu-research-agent/bio:gpu-latest python -c "import rapids_singlecell as rsc, cupy as cp; print('rsc', rsc.__version__); print('cuda', cp.cuda.is_available()); print('scanpy OK'); import scanpy"`
Expected: `rsc <版本>` / `cuda True` / scanpy import 无错

同时记录镜像体积：`docker images feishu-research-agent/bio:gpu-latest`（验收 < 6GB）

- [ ] **Step 4: Commit**

```bash
git add sandbox/bio-gpu.Dockerfile
git commit -m "build(phase25): bio:gpu-latest 镜像（rapids-singlecell 栈）"
```

---

### Task 4: process.py / markers.py GPU 分支 + GPU 镜像实测对照

**Files:**
- Modify: `sandbox/sc_tools/process.py`
- Modify: `sandbox/sc_tools/markers.py`

- [ ] **Step 1: process.py 双栈改造**

完整新文件内容（改动点：import 探测 + PCA/neighbors/UMAP/leiden 分支 + accelerator 输出；GPU 分支 scale 不带 max_value——spec §1.2 实现约束）：

```python
"""sc_process：归一化→HVG→scale→PCA→邻居→UMAP→Leiden（Phase 20/25）。

stdin: {"dataset_id": ..., "n_top_hvg": 2000, "n_pcs": 50,
        "n_neighbors": 15, "resolution": 1.0}
产出 processed.h5ad + umap.png；无 filtered.h5ad 时用 raw.h5ad 内置默认过滤。
Phase 25：import 探测 rapids_singlecell——GPU 镜像走 rsc 加速分支
（PCA/neighbors/UMAP/leiden），CPU 镜像行为与 Phase 20 完全一致。
"""
from __future__ import annotations

from common import WS_ROOT, emit, load_adata, run, read_args


def main() -> None:
    import matplotlib.pyplot as plt
    import scanpy as sc

    try:
        import rapids_singlecell as rsc
        gpu = True
    except ImportError:
        rsc = None
        gpu = False

    args = read_args()
    n_top_hvg = int(args.get("n_top_hvg", 2000))
    n_pcs = int(args.get("n_pcs", 50))
    n_neighbors = int(args.get("n_neighbors", 15))
    resolution = float(args.get("resolution", 1.0))

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "any"})

    # raw（未跑 sc_qc）→ 内置默认过滤（spec §4 两步快速路径）
    had_filtered = (WS_ROOT / args["dataset_id"] /
                    "filtered.h5ad").exists()
    if not had_filtered:
        sc.pp.filter_cells(adata, min_genes=600)
        sc.pp.filter_genes(adata, min_cells=3)

    # 标准流程（归一化→HVG 两路径一致；scale 起分栈）
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top_hvg, flavor="seurat")
    adata.raw = adata
    adata = adata[:, adata.var["highly_variable"]].copy()
    n_comps = min(n_pcs, adata.n_vars - 1, adata.n_obs - 1)
    if gpu:
        # GPU 分支：rmm 不手动配置、scale 不带 max_value（skill 实战记录）
        sc.pp.scale(adata)
        rsc.pp.pca(adata, n_comps=n_comps)
        rsc.pp.neighbors(adata, n_neighbors=n_neighbors)
        rsc.tl.umap(adata)
        rsc.tl.leiden(adata, resolution=resolution)
    else:
        sc.pp.scale(adata, max_value=10)
        sc.tl.pca(adata, n_comps=n_comps, svd_solver="arpack")
        sc.pp.neighbors(adata, n_neighbors=n_neighbors)
        sc.tl.umap(adata)
        sc.tl.leiden(adata, resolution=resolution, flavor="igraph",
                     n_iterations=2, directed=False)

    n_clusters = int(adata.obs["leiden"].nunique())
    cluster_sizes = adata.obs["leiden"].value_counts().to_dict()

    # UMAP 图（英文标签：基因名/cluster 天然英文，spec §11 字体风险）
    fig, ax = plt.subplots(figsize=(7, 5), dpi=150)
    sc.pl.umap(adata, color="leiden", ax=ax, show=False, legend_loc="on data",
               title=f"UMAP (leiden, res={resolution})")
    ds_dir = WS_ROOT / args["dataset_id"]
    umap_png = ds_dir / "umap.png"
    fig.savefig(umap_png, bbox_inches="tight")
    plt.close(fig)

    adata.write_h5ad(ds_dir / "processed.h5ad")
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_cells": int(adata.n_obs),
        "n_clusters": n_clusters,
        "cluster_sizes": {str(k): int(v) for k, v in cluster_sizes.items()},
        "used_input": "filtered" if had_filtered else "raw(default_qc)",
        "accelerator": "gpu" if gpu else "cpu",
        "umap_png": str(umap_png),
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: markers.py 双栈改造**

完整新文件内容（GPU 分支：rsc.tl.rank_genes_groups 用 use_raw=False——rsc 不支持 raw 槽，仅 HVG 基因参与排名，spec §1.2 已记；其余输出结构不变）：

```python
"""sc_markers：每簇差异基因（rank_genes_groups）+ dotplot（Phase 20/25）。

stdin: {"dataset_id": ..., "method": "wilcoxon", "top_n": 10}
需 processed.h5ad（无则报错提示先跑 sc_process）。
Phase 25：GPU 镜像走 rsc.tl.rank_genes_groups（use_raw=False，HVG 尺度）；
CPU 镜像维持 use_raw=True（归一化 log 全基因快照）。
"""
from __future__ import annotations

from common import WS_ROOT, emit, load_adata, run, read_args


def main() -> None:
    import matplotlib.pyplot as plt
    import pandas as pd
    import scanpy as sc

    try:
        import rapids_singlecell as rsc
        gpu = True
    except ImportError:
        rsc = None
        gpu = False

    args = read_args()
    method = str(args.get("method", "wilcoxon"))
    top_n = int(args.get("top_n", 10))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})

    if gpu:
        # rsc 不支持 use_raw=True；HVG 尺度差异分析（skill 实战记录）
        rsc.tl.rank_genes_groups(adata, groupby="leiden", method=method,
                                 use_raw=False)
    else:
        # 差异分析用原始计数尺度（adata.raw：归一化 log 后全基因快照）
        sc.tl.rank_genes_groups(adata, groupby="leiden", method=method,
                                use_raw=True)

    # 每簇 top 基因（名字/score/logFC）
    result = adata.uns["rank_genes_groups"]
    groups = [str(g) for g in result["names"].dtype.names]
    markers = {}
    for g in groups:
        genes = result["names"][g][:top_n]
        scores = result["scores"][g][:top_n]
        logfc = result["logfoldchanges"][g][:top_n]
        markers[g] = [
            {"gene": str(gn), "score": round(float(s), 2),
             "log2fc": round(float(lf), 2)}
            for gn, s, lf in zip(genes, scores, logfc)
        ]

    # dotplot 图（top5/簇，标签天然英文）
    fig = sc.pl.rank_genes_groups_dotplot(
        adata, n_genes=5, show=False, return_fig=True,
        standard_scale="var")
    ds_dir = WS_ROOT / args["dataset_id"]
    dotplot_png = ds_dir / "dotplot.png"
    fig.savefig(dotplot_png, bbox_inches="tight")
    plt.close("all")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        "n_clusters": len(groups),
        "markers": markers,
        "accelerator": "gpu" if gpu else "cpu",
        "dotplot_png": str(dotplot_png),
    })


if __name__ == "__main__":
    run(main)
```

注意：pandas import 若 lint 报未使用可删（原文件有，保持原样不折腾）。

- [ ] **Step 3: CPU 镜像回归（双栈改动不能破坏 CPU 路径）**

重建 CPU 镜像并跑既有冒烟 sc 链（脚本 process/markers 有改动必须重建镜像）：

Run: `docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile`
Run: `.venv\Scripts\python.exe scripts\smoke_sc_chain.py`
Expected: 全链 PASS（accelerator 新字段出现且为 cpu）

- [ ] **Step 4: 重建 GPU 镜像（COPY 的脚本变了）+ tiny 数据 GPU 实测**

Run: `docker build -t feishu-research-agent/bio:gpu-latest sandbox -f sandbox/bio-gpu.Dockerfile`

用 handler 层端到端跑 GPU（等价真机路径，settings 开关全开）：

```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from orchestrator.app import build_registry; from config.settings import settings; settings.bio_use_gpu=True; reg=build_registry(settings); h=reg.get('sc_load').handler; import json; r=h(path='data/tiny_scrna.h5ad'); print('load', json.dumps(r)[:200]); ref=r['dataset_ref']; r=reg.get('sc_process').handler(dataset_ref=ref); print('process', json.dumps(r)[:400]); r=reg.get('sc_markers').handler(dataset_ref=ref); print('markers', json.dumps(r)[:400])"
```

注意：build_registry 的真实函数名/签名以 orchestrator/app.py 为准（先 Read 确认组装入口；若 settings 是 frozen 的则用环境变量 `$env:BIO_USE_GPU='true'` 启动新进程）。

Expected：process 输出含 `"accelerator": "gpu"`、n_clusters=3；markers 输出三簇 markers 含 MARKER_D1/D2/D3 方向（GPU use_raw=False 仅 HVG，marker 应在 HVG 内）；无 CUDA 报错。

- [ ] **Step 5: CPU/GPU 结果对照（统计一致性，spec §5）**

同一 tiny_scrna 分别 CPU（Step 3 冒烟数据集 ref 可从 smoke 输出拿，或重跑一遍 CPU handler 链）与 GPU 各跑一次，对照：n_clusters 一致（=3）、各簇 top10 markers 集合重合度 ≥ 70%（浮点实现差异容许少量序位不同）。写一段临时 python 在终端比对两个 dataset_ref 的 markers 输出即可，不入库。

- [ ] **Step 6: Commit**

```bash
git add sandbox/sc_tools/process.py sandbox/sc_tools/markers.py
git commit -m "feat(phase25): sc_tools 双栈自适应（rapids-singlecell GPU 分支）"
```

---

### Task 5: 收尾——全量回归 + 冒烟 + 文档 + ws_client 重启验证

**Files:**
- Modify: `docs/ROADMAP.md`（@59-60 Phase 20 后续扩展 GPU 项勾掉 + 变更记录）
- Modify: `测试总结+2026-09-02T12-26-38.md`（追加 Phase 25 节）

- [ ] **Step 1: 全量回归（默认 bio_use_gpu=false，行为零变化）**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/ -q`
Expected: 846 passed（840+T1 的 2+T2 的 4；以实际为准），warnings 不变

- [ ] **Step 2: ws_client 重启（--force 接管）加载新 settings 与 handler**

Run: `.\.venv\Scripts\python.exe -m gateway.ws_client --force`（long-running，blocking=false）
Expected: 日志含 `--force: terminating old ws_client` → `=== TRAE SOLO WS Client Started ===` → `Lark connected`，单实例。

注意：bio_use_gpu 默认 false——真机默认仍走 CPU。GPU 真机试用方式：`.env` 加 `BIO_USE_GPU=true` 后重启。这一步只做"开关关闭下机器人正常"，GPU 真机留给用户择机。

- [ ] **Step 3: ROADMAP 更新**

`docs/ROADMAP.md` @59-60：

```markdown
- **Phase 20 后续扩展**：
  - GPU 镜像 bio:gpu-latest ~~（rapids-singlecell；spec 已排期，触发时机=大队列需求）~~ → **Phase 25 已交付**（2026-09-02，sc_process/sc_markers GPU 分支 + bio_use_gpu 开关）
```

变更记录表追加一行：

```markdown
| 2026-09-02 | Phase 25 GPU 镜像 bio:gpu-latest：BioRunner --gpus 透传 + bio_use_gpu 开关 + sc_tools 双栈自适应（rapids-singlecell），RTX 3090 实测通过 |
```

- [ ] **Step 4: 测试总结追加**

`测试总结+2026-09-02T12-26-38.md` 文件尾追加（时间戳执行时填）：

```markdown
---

# Phase 25 GPU 镜像 bio:gpu-latest（2026-09-02 <执行时间>）

## 环境
- RTX 3090 24GB / 驱动 591.74 / CUDA 13.1；docker --gpus all 透传实测可用

## 交付
- BioRunner --gpus 透传（commit <T1>）
- bio_use_gpu/bio_gpu_image 开关 + sc_process/sc_markers 分流（commit <T2>）
- bio:gpu-latest 镜像（rapids-singlecell，<体积>）（commit <T3>）
- process.py/markers.py 双栈自适应（commit <T4>）

## 验证
- 全量回归 <N> passed（默认开关关闭，行为零变化）
- CPU 镜像重建 + 冒烟 sc 链 PASS（双栈改动未破坏 CPU 路径）
- GPU 实测：accelerator=gpu、n_clusters=<n>、markers 方向正确
- CPU/GPU 统计一致性：簇数一致、top10 markers 重合度 <x>%

## 后续建议
- 大队列数据（>50k 细胞）真机试用 GPU：.env BIO_USE_GPU=true 重启即可
- 若 CUDA OOM：考虑 markers 仍回 CPU / 降 HVG 数（spec §3 显性报错原则）
```

- [ ] **Step 5: Commit**

```bash
git add docs/ROADMAP.md "测试总结+2026-09-02T12-26-38.md"
git commit -m "docs(phase25): 收官——ROADMAP GPU 项勾掉 + 测试总结"
```

---

## Self-Review 记录

- **Spec 覆盖**：§1.1 镜像→T3；§1.2 双栈脚本→T4；§1.3 --gpus 透传→T1；
  §1.4 settings→T2；§1.5 handler 分流→T2；§3 错误处理（显性上抛）→无代码，
  原则记入 T4 实现约束；§4.1 单测→T1/T2；§4.2 镜像级验证→T3/T4；
  §4.3 回归→T5；§5 验收→T3 体积/T4 对照/T5 回归
- **类型一致性**：run(..., gpus=False) T1 定义、T2 调用一致；_accel() 返回
  (str|None, bool)，run(image=None) 走 runner 默认镜像——与 T1 实现兼容
- **占位符扫描**：T4 Step 4 的 build_registry 入口标注了"先 Read 确认"
  （runtime 组装函数名未在本计划核实，给子代理留了明确指引而非伪代码）；
  T5 时间戳/commit hash/体积为执行期填空，属占位合理项
- **降级路径**：T3 清华源缺 wheel → 双源 → nvidia/cuda base（需父代理商议）
  已写明，避免子代理自行扩改
