# Phase 37 WNN 多组学 + 虚拟敲除 实施计划（spec+plan 合并）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 sc_wnn（muon WNN 多组学整合，产新 dataset_ref 接入全生态）与 sc_knockout（scTenifoldKnk 虚拟敲除，R 容器保真路线），sc_* 工具数 21→23。GHIST 暂缓记 ROADMAP（GPU+Xenium 数据齐备后立项）。

**Architecture:** 沿用 BioRunner 约定。wnn：双文件白名单挂载（两 resolve_data_path，同根去重），新 dataset_id = `compute_dataset_id(rna)[:6]+compute_dataset_id(adt)[:6]`（幂等键先例）；knockout：Python 脚本导 dense CSV → 容器内 Rscript 子进程跑 knk.R → 读回 diffRegulation.csv。

**探针已钉死的事实（必须落实）**：
1. muon 0.1.9+mudata 0.4.1 宿主实测兼容（pandas 2.3.3/numpy 2.5.2 未动）；**`n_multineighbors ≥ n_cells` 会堆腐化崩溃** → 脚本内强制 `min(200, n_obs-1)`；各模态**先 `sc.pp.neighbors`** 再 `mu.pp.neighbors`；PCA `n_comps < min(n_obs, n_vars)` 钳制；mod_weight 实际在 `mdata.obs["rna:mod_weight"]` 分列
2. 镜像基底 **Debian trixie**，apt r-base-core=4.5.0；scTenifoldKnk 1.1 在 CRAN（纯 R），但依赖链含需编译包 → R 层装 r-base-dev+g++ 同层装完即 purge；P3M trixie 源可达（设 HTTPUserAgent 优先二进制）
3. pytest `--basetemp=.pytest_tmp`；回归落 `.pytest_last.log`；venv `.\.venv\Scripts\python.exe`
4. 冒烟 monkeypatch：值绑定需双补丁（common 与各脚本模块）；宿主无 R → knockout 冒烟走容器（镜像先行），wnn 冒烟走宿主

---

### Task 1: wnn.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/wnn.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_wnn：WNN 多组学整合（Phase 37，muon；对齐 Seurat FindMultiModalNeighbors）。

stdin: {"dataset_id": <handler 端算好的新 id>, "rna_path": "<rel>",
        "adt_path": "<rel>", "rna_dims": 30, "adt_dims": 18,
        "resolution": 1.0, "n_neighbors": 20, "seed": 42}
输入双 h5ad（RNA counts + ADT counts，按 obs_names 交集对齐）。各模态
先 sc.pp.neighbors（muon 强制前置），RNA normalize/log1p/HVG/scale/PCA、
ADT CLR/PCA，再 mu.pp.neighbors WNN 联合图 → umap → leiden。
muon 坑防御（探针实测）：n_multineighbors >= n_obs 时 pynndescent 返回
-1 索引致堆腐化 → 强制 min(200, n_obs-1)；PCA n_comps 钳制 <
min(n_obs, n_vars)。
产物：新数据集 WS/{id}/processed.h5ad（raw=RNA lognorm 全基因 + obsm
X_umap=wnn UMAP + obs leiden + 模态权重列）与 raw.h5ad（RNA counts），
直接接入 sc_plot/sc_score/sc_annotate 等下游；另落 weights csv 与
UMAP png。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import DATA_ROOT, WS_ROOT, emit, read_args, run


def _read(rel: str):
    """从 /data 挂载读 h5ad（resolve_data_path 白名单已在 handler 层把关）。"""
    import anndata as ad

    p = (DATA_ROOT / rel).resolve()
    if DATA_ROOT not in p.parents:
        raise ValueError(f"invalid path: {rel!r}")
    return ad.read_h5ad(p)


def _prep_rna(adata, dims: int):
    """RNA 模态：归一化→log1p→HVG→scale→PCA→neighbors（muon 前置要求）。"""
    import scanpy as sc

    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=min(2000, adata.n_vars),
                                flavor="seurat")
    hvg = adata[:, adata.var["highly_variable"]].copy()
    sc.pp.scale(hvg, max_value=10)
    npc = max(2, min(dims, hvg.n_obs - 1, hvg.n_vars - 1))
    sc.tl.pca(hvg, n_comps=npc)
    sc.pp.neighbors(hvg, n_neighbors=min(15, hvg.n_obs - 1))
    return adata, hvg  # adata 持有 lognorm 全基因（作 raw），hvg 供 WNN


def _prep_adt(adata, dims: int):
    """ADT 模态：CLR 归一化 → PCA → neighbors。"""
    import muon as mu
    import scanpy as sc

    mu.prot.pp.clr(adata)
    npc = max(2, min(dims, adata.n_obs - 1, adata.n_vars - 1))
    sc.tl.pca(adata, n_comps=npc)
    sc.pp.neighbors(adata, n_neighbors=min(15, adata.n_obs - 1))
    return adata


def main() -> None:
    """主流程：双模态预处理 → WNN → 联合 UMAP/leiden → 新数据集落盘。"""
    import matplotlib.pyplot as plt
    import muon as mu

    args = read_args()
    rna_dims = int(args.get("rna_dims", 30))
    adt_dims = int(args.get("adt_dims", 18))
    resolution = float(args.get("resolution", 1.0))
    n_neighbors = int(args.get("n_neighbors", 20))
    seed = int(args.get("seed", 42))

    rna_raw = _read(str(args["rna_path"]))
    adt = _read(str(args["adt_path"]))
    cells = rna_raw.obs_names.intersection(adt.obs_names)
    if len(cells) < 50:
        raise ValueError(f"too few shared cells between RNA/ADT: "
                         f"{len(cells)} (<50)")
    rna_counts = rna_raw[cells]   # counts 层留作 raw.h5ad
    rna, rna_hvg = _prep_rna(rna_counts.copy(), rna_dims)
    adt = _prep_adt(adt[cells].copy(), adt_dims)

    mdata = mu.MuData({"rna": rna_hvg, "adt": adt})
    # 探针实测坑：n_multineighbors >= n_obs 时 pynndescent 返回 -1 索引
    # → scipy 堆腐化。强制 < n_obs。
    mu.pp.neighbors(mdata, n_neighbors=min(n_neighbors, len(cells) - 1),
                    n_multineighbors=min(200, len(cells) - 1),
                    n_bandwidth_neighbors=20, metric="euclidean",
                    random_state=seed)
    mu.tl.umap(mdata, random_state=seed)
    mu.tl.leiden(mdata, resolution=resolution, random_state=seed)

    dataset_id = str(args["dataset_id"])
    out_dir = WS_ROOT / dataset_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 新数据集 processed.h5ad：raw=RNA lognorm 全基因，接入下游生态
    out = rna.copy()  # lognorm 全基因
    out.raw = out
    out.obs = rna.obs.copy()
    out.obs["leiden"] = mdata.obs["leiden"].astype(str).to_numpy()
    for mod in ("rna", "adt"):
        col = f"{mod}:mod_weight"
        if col in mdata.obs:
            out.obs[f"wnn_weight_{mod}"] = mdata.obs[col].astype(float) \
                .to_numpy()
    out.obsm["X_umap"] = mdata.obsm["X_umap"]
    out.uns["wnn"] = {"rna_dims": rna_dims, "adt_dims": adt_dims,
                      "resolution": resolution}
    out.write(out_dir / "processed.h5ad")
    rna_counts.write(out_dir / "raw.h5ad")

    weights = out.obs[[c for c in out.obs.columns
                       if c.startswith("wnn_weight_")]].describe().loc["mean"]
    csv_path = out_dir / "wnn_modality_weights.csv"
    out.obs[["leiden"] + [c for c in out.obs.columns
                          if c.startswith("wnn_weight_")]].to_csv(csv_path)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    umap = out.obsm["X_umap"]
    labs = out.obs["leiden"]
    cats = sorted(labs.unique().tolist())
    cmap = plt.get_cmap("tab20")
    for i, c in enumerate(cats):
        m = (labs == c).to_numpy()
        axes[0].scatter(umap[m, 0], umap[m, 1], s=5, color=cmap(i % 20),
                        label=f"{c} ({int(m.sum())})", linewidths=0)
    axes[0].legend(fontsize=7, markerscale=2)
    axes[0].set_title("WNN leiden", fontsize=10)
    for ax, mod in ((axes[1], "rna"), (axes[2], "adt")):
        col = f"wnn_weight_{mod}"
        if col in out.obs:
            sc_plt = ax.scatter(umap[:, 0], umap[:, 1], s=5,
                                c=out.obs[col], cmap="viridis",
                                linewidths=0)
            fig.colorbar(sc_plt, ax=ax, shrink=0.8)
        ax.set_title(f"modality weight: {mod.upper()}", fontsize=10)
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    fig.tight_layout()
    png_path = out_dir / "wnn_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    emit({
        "ok": True,
        "dataset_ref": dataset_id,
        "n_cells": int(out.n_obs),
        "n_genes": int(out.n_vars),
        "n_proteins": int(adt.n_vars),
        "n_clusters": int(len(cats)),
        "mean_modality_weights": {str(k): round(float(v), 3)
                                  for k, v in weights.items()},
        "weights_csv": str(csv_path),
        "umap_png": str(png_path),
        "saved": str(out_dir / "processed.h5ad"),
        "note": "新 dataset_ref 可直接用于 sc_plot/sc_score/sc_annotate/"
                "sc_markers 等下游（raw=RNA lognorm）",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/wnn.py`
Expected: 零告警

---

### Task 2: knockout.py + knk.R

**Files:**
- Create: `sandbox/sc_tools/knockout.py`
- Create: `sandbox/r_tools/knk.R`

- [ ] **Step 1: knockout.py**

```python
"""sc_knockout：虚拟敲除（Phase 37，R 包 scTenifoldKnk 保真路线）。

stdin: {"dataset_id": ..., "gko": "SPI1", "celltype_col": "",
        "group": "", "n_genes": 1000, "n_net": 10, "n_cells": 500,
        "min_lib_size": 1000, "mt_threshold": 0.1}
counts 走 filtered/raw 链；可按 celltype_col==group 子集（obs 取自
processed，交集对齐）；HVG top n_genes → dense CSV → 容器内 Rscript
跑 knk.R（scTenifoldKnk：pcNet→张量分解→流形对齐→dRegulation）→
读回 diffRegulation.csv → 火山图。产物落 knockout/。耗时分钟~小时级
（nNet 次网络构建是重头）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run

R_SCRIPT = Path("/opt/r_tools/knk.R")


def main() -> None:
    """主流程：子集+HVG → dense CSV → Rscript knk → 读回 + 火山图。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    gko = str(args["gko"]).strip()
    n_genes = int(args.get("n_genes", 1000))
    celltype_col = str(args.get("celltype_col", "")).strip()
    group = str(args.get("group", "")).strip()

    counts_ad = load_adata({"dataset_id": args["dataset_id"],
                            "file": "any"})
    if celltype_col and group:
        proc = load_adata({"dataset_id": args["dataset_id"],
                           "file": "processed"})
        if celltype_col not in proc.obs:
            raise ValueError(f"celltype column {celltype_col!r} not in "
                             f"processed obs")
        keep = proc.obs_names[proc.obs[celltype_col].astype(str) == group]
        cells = counts_ad.obs_names.intersection(keep)
        if len(cells) < 100:
            raise ValueError(f"group {group!r} has {len(cells)} cells "
                             f"(<100)")
        counts_ad = counts_ad[cells]

    # HVG：lognorm 副本上选，矩阵仍用 counts（scTenifoldKnk 输入为 counts）
    norm = counts_ad.copy()
    sc.pp.normalize_total(norm, target_sum=1e4)
    sc.pp.log1p(norm)
    sc.pp.highly_variable_genes(norm, n_top_genes=min(n_genes, norm.n_vars),
                                flavor="seurat")
    genes = norm.var_names[norm.var["highly_variable"]].astype(str)
    if gko not in genes:
        near = [g for g in norm.var_names.astype(str)
                if g.upper() == gko.upper() or gko.upper() in g.upper()]
        raise ValueError(f"gKO {gko!r} not in HVG set (n={len(genes)}); "
                         f"near matches in data: {near[:10]}")
    sub = counts_ad[:, genes]
    X = sub.X.toarray() if hasattr(sub.X, "toarray") else np.asarray(sub.X)
    mat = pd.DataFrame(X.T, index=genes, columns=sub.obs_names.astype(str))

    out_dir = WS_ROOT / args["dataset_id"] / "knockout" / gko
    out_dir.mkdir(parents=True, exist_ok=True)
    input_csv = out_dir / "input_counts.csv"
    mat.to_csv(input_csv)

    r_cmd = [
        "Rscript", str(R_SCRIPT), str(input_csv), str(out_dir), gko,
        str(int(args.get("n_net", 10))),
        str(min(int(args.get("n_cells", 500)), mat.shape[1])),
        str(int(args.get("min_lib_size", 1000))),
        str(float(args.get("mt_threshold", 0.1))),
    ]
    proc_r = subprocess.run(r_cmd, capture_output=True, text=True,
                            timeout=3300)
    if proc_r.returncode != 0:
        raise RuntimeError(f"Rscript knk.R failed: {proc_r.stderr[-1500:]}")
    dr_path = out_dir / "diffRegulation.csv"
    if not dr_path.exists():
        raise RuntimeError(f"knk.R did not produce diffRegulation.csv; "
                           f"stdout tail: {proc_r.stdout[-500:]}")

    dr = pd.read_csv(dr_path)
    dr.columns = [c.strip() for c in dr.columns]
    gene_col = next((c for c in dr.columns if c.lower() in
                     ("gene", "genes", "x")), dr.columns[0])
    z_col = next(c for c in dr.columns if c.lower() == "z")
    p_col = next((c for c in dr.columns if c.lower() in
                  ("p.adj", "padj", "p_adj")), None)
    dr = dr.rename(columns={gene_col: "gene"})
    dr = dr.sort_values(z_col, ascending=False)

    fig, ax = plt.subplots(figsize=(6.5, 5))
    y = -np.log10(dr[p_col].clip(lower=1e-300)) if p_col \
        else dr[z_col]
    ax.scatter(dr[z_col], y, s=6, c="#888888", linewidths=0)
    top = dr.head(10)
    ax.scatter(top[z_col],
               (-np.log10(top[p_col].clip(lower=1e-300)) if p_col
                else top[z_col]),
               s=14, c="#d84b3b", linewidths=0)
    for _, row in top.iterrows():
        ax.annotate(str(row["gene"]),
                    (row[z_col],
                     -np.log10(max(row[p_col], 1e-300)) if p_col
                     else row[z_col]),
                    fontsize=6, alpha=0.8)
    ax.set_xlabel("dRegulation Z")
    ax.set_ylabel("-log10(p.adj)" if p_col else "Z")
    ax.set_title(f"virtual KO: {gko} ({mat.shape[1]} cells, "
                 f"{mat.shape[0]} genes)", fontsize=10)
    fig.tight_layout()
    png_path = out_dir / "knockout_volcano.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "gko": gko,
        "n_cells": int(mat.shape[1]),
        "n_genes": int(mat.shape[0]),
        "group": f"{celltype_col}=={group}" if group else "",
        "top_dr_genes": [str(g) for g in dr["gene"].head(10)],
        "dr_csv": str(dr_path),
        "volcano_png": str(png_path),
        "note": "scTenifoldKnk R 包保真链路；diffRegulation 全表见 csv",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: knk.R**

```r
# scTenifoldKnk 虚拟敲除批处理（Phase 37）。
# argv: input_csv out_dir gko n_net n_cells min_lib_size mt_threshold
# 输入 genes x cells counts CSV（Python 侧已做 HVG 与子集）；
# 输出 out_dir/diffRegulation.csv。
args <- commandArgs(trailingOnly = TRUE)
input_csv <- args[1]
out_dir <- args[2]
gko <- args[3]
n_net <- as.integer(args[4])
n_cells <- as.integer(args[5])
min_lib <- as.numeric(args[6])
mt_thr <- as.numeric(args[7])

suppressMessages(library(scTenifoldKnk))

mat <- as.matrix(read.csv(input_csv, row.names = 1, check.names = FALSE))
storage.mode(mat) <- "numeric"

set.seed(42)
res <- scTenifoldKnk(
  countMatrix = mat,
  gKO = gko,
  qc_maxMTratio = mt_thr,
  qc_minLibSize = min_lib,
  nc_nNet = n_net,
  nc_nCells = n_cells,
  nc_nComp = 3,
  nc_scaleScores = TRUE,
  nc_symmetric = FALSE,
  nc_q = 0.9,
  td_K = 3,
  td_maxIter = 1000,
  td_maxError = 1e-5,
  td_nDecimal = 2,
  ma_nDim = 2
)

dr <- res$diffRegulation
write.csv(dr, file.path(out_dir, "diffRegulation.csv"), row.names = FALSE)
cat("KOK_DONE", nrow(dr), "\n")
```

- [ ] **Step 3: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/knockout.py`
Expected: 零告警

---

### Task 3: 注册 2 个 ToolSpec + handler

**Files:**
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`（docstring 行 1；handler 追加在 sc_scenic 之后；ToolSpec 追加在 sc_scenic ToolSpec 之后）

- [ ] **Step 1: docstring**：`Phase 20/31/32/33/34/35/36` → `Phase 20/31/32/33/34/35/36/37`，`21 个` → `23 个`

- [ ] **Step 2: 追加两个 handler（sc_scenic handler 之后）**

```python
    def sc_wnn(*, rna_file: str, adt_file: str, rna_dims: int = 30,
               adt_dims: int = 18, resolution: float = 1.0,
               n_neighbors: int = 20, seed: int = 42) -> dict:
        """WNN 多组学整合（Phase 37，muon）：双文件 → 新 dataset_ref。"""
        try:
            mount_r, rel_r, host_r = runner.resolve_data_path(rna_file)
            mount_a, rel_a, host_a = runner.resolve_data_path(adt_file)
            dataset_id = (compute_dataset_id(host_r)[:6]
                          + compute_dataset_id(host_a)[:6])
            mounts = [(mount_r, "/data")]
            if mount_a != mount_r:
                mounts = [(mount_r, "/data_rna"), (mount_a, "/data_adt")]
            payload = {
                "dataset_id": dataset_id,
                "rna_path": rel_r if mount_a == mount_r else rel_r,
                "adt_path": rel_a,
                "rna_dims": rna_dims, "adt_dims": adt_dims,
                "resolution": resolution, "n_neighbors": n_neighbors,
                "seed": seed,
            }
            if mount_a == mount_r:
                out = runner.run("wnn", payload,
                                 mounts=[(mount_r, "/data")],
                                 timeout_sec=1200)
            else:
                # 异根：脚本 DATA_ROOT 指向 /data_rna，ADT 换绝对容器路径
                payload["rna_path"] = rel_r
                payload["adt_path"] = f"/data_adt/{rel_a}"
                out = runner.run("wnn", payload,
                                 mounts=[(mount_r, "/data"),
                                         (mount_a, "/data_adt")],
                                 timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_knockout(*, dataset_ref: str, gko: str,
                    celltype_col: str = "", group: str = "",
                    n_genes: int = 1000, n_net: int = 10,
                    n_cells: int = 500, min_lib_size: int = 1000,
                    mt_threshold: float = 0.1) -> dict:
        """虚拟敲除（Phase 37，scTenifoldKnk R 保真链路）。"""
        try:
            out = runner.run("knockout", {
                "dataset_id": dataset_ref, "gko": gko,
                "celltype_col": celltype_col, "group": group,
                "n_genes": n_genes, "n_net": n_net, "n_cells": n_cells,
                "min_lib_size": min_lib_size, "mt_threshold": mt_threshold,
            }, timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

注意：异根挂载分支里 adt_path 传容器绝对路径 `/data_adt/<rel>`——对应 wnn.py `_read()` 需容忍以 `/` 开头的"容器绝对路径"（该路径不校验 DATA_ROOT，直接读）。若发现与 _read 的白名单校验冲突，最小修复：`_read` 开头加 `if rel.startswith("/"): return ad.read_h5ad(Path(rel))`，同步进 sandbox 脚本并在回归后报告。

- [ ] **Step 3: 追加两个 ToolSpec（sc_scenic ToolSpec 之后）**

```python
    registry.register(ToolSpec(
        name="sc_wnn",
        description=(
            "WNN 多组学整合（Phase 37，muon，等价 Seurat "
            "FindMultiModalNeighbors）：CITE-seq 场景把 RNA 与蛋白（ADT）"
            "两个模态加权整合成联合邻居图 → 联合 UMAP + leiden 聚类 + "
            "每细胞模态权重。输入两个 h5ad 文件（RNA counts + ADT counts，"
            "按细胞名交集对齐），输出**新的 dataset_ref**（raw=RNA "
            "lognorm，可直接接 sc_plot/sc_score/sc_annotate/sc_markers "
            "下游）。两文件须在数据目录白名单内。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "rna_file": {"type": "string",
                             "description": "RNA h5ad 本地路径（counts）"},
                "adt_file": {"type": "string",
                             "description": "ADT h5ad 本地路径（蛋白 counts）"},
                "rna_dims": {"type": "integer", "default": 30,
                             "description": "RNA PCA 维数"},
                "adt_dims": {"type": "integer", "default": 18,
                             "description": "ADT PCA 维数"},
                "resolution": {"type": "number", "default": 1.0},
                "n_neighbors": {"type": "integer", "default": 20},
                "seed": {"type": "integer", "default": 42},
            },
            "required": ["rna_file", "adt_file"],
        },
        risk_level="L1_compute",
        handler=sc_wnn,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_knockout",
        description=(
            "虚拟敲除（Phase 37，scTenifoldKnk R 保真链路）：不敲真基因，"
            "在网络上模拟敲掉某个转录因子，输出全基因组差异调控排序"
            "（dRegulation Z/FC/p/padj + 火山图），回答\"敲掉 X 会影响哪些"
            "基因\"。gKO 为高变基因内的基因名；可用 celltype_col+group 只"
            "在指定细胞类型内做（≥100 细胞）。耗时分钟~小时级（n_net 次"
            "网络构建）。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "gko": {"type": "string",
                        "description": "要虚拟敲除的基因（须在 HVG 内）"},
                "celltype_col": {"type": "string", "default": "",
                                 "description": "子集用标签列（可空）"},
                "group": {"type": "string", "default": "",
                          "description": "子集用标签值（可空=全数据）"},
                "n_genes": {"type": "integer", "default": 1000,
                            "description": "HVG 数（网络规模，越大越慢）"},
                "n_net": {"type": "integer", "default": 10,
                          "description": "子抽样网络数（耗时线性）"},
                "n_cells": {"type": "integer", "default": 500,
                            "description": "每网抽细胞数"},
                "min_lib_size": {"type": "integer", "default": 1000,
                                 "description": "R 侧 QC 最小文库大小"},
                "mt_threshold": {"type": "number", "default": 0.1},
            },
            "required": ["dataset_ref", "gko"],
        },
        risk_level="L1_compute",
        handler=sc_knockout,
        timeout_sec=3600,
    ))
```

- [ ] **Step 4: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check orchestrator/tools/builtin/l3_singlecell.py`
Expected: 零告警

---

### Task 4: 单元测试（6 用例）

**Files:**
- Modify: `tests/unit/test_l3_singlecell.py`（文件尾追加）

- [ ] **Step 1: 追加 6 个用例**

```python
# === Phase 37：WNN 多组学 + 虚拟敲除 ===


def test_sc_phase37_tools_registered_l1(tmp_path):
    """两新工具注册可见、L1_compute、超时正确。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_wnn", "sc_knockout"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"
    assert reg.get("sc_wnn").timeout_sec == 1200
    assert reg.get("sc_knockout").timeout_sec == 3600


def test_sc_wnn_same_root_single_mount(tmp_path):
    """两文件同数据根 → 单 /data 挂载；新 dataset_id 为双 hash 拼接。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    root = Path("D:/sc_data")
    runner.resolve_data_path.side_effect = [
        (root, "rna.h5ad", "D:/sc_data/rna.h5ad"),
        (root, "adt.h5ad", "D:/sc_data/adt.h5ad"),
    ]
    runner.run.return_value = {"ok": True, "dataset_ref": "abc123def456",
                               "n_cells": 900, "n_clusters": 8}
    out = reg.get("sc_wnn").handler(rna_file="D:/sc_data/rna.h5ad",
                                    adt_file="D:/sc_data/adt.h5ad")
    call = runner.run.call_args
    assert call.args[0] == "wnn"
    assert len(call.kwargs["mounts"]) == 1
    assert call.kwargs["mounts"][0] == (root, "/data")
    assert len(call.args[1]["dataset_id"]) == 12
    assert call.kwargs["timeout_sec"] == 1200
    assert out["n_clusters"] == 8 and "ok" not in out


def test_sc_wnn_cross_root_dual_mount(tmp_path):
    """两文件异数据根 → 双挂载 + adt_path 容器绝对路径。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.side_effect = [
        (Path("D:/sc_data"), "rna.h5ad", "D:/sc_data/rna.h5ad"),
        (Path("E:/adt"), "adt.h5ad", "E:/adt/adt.h5ad"),
    ]
    runner.run.return_value = {"ok": True, "dataset_ref": "x" * 12}
    reg.get("sc_wnn").handler(rna_file="D:/sc_data/rna.h5ad",
                              adt_file="E:/adt/adt.h5ad")
    call = runner.run.call_args
    targets = sorted(m[1] for m in call.kwargs["mounts"])
    assert targets == ["/data", "/data_adt"]
    assert call.args[1]["adt_path"].startswith("/data_adt/")


def test_sc_wnn_schema_required(tmp_path):
    """required 锁定双文件参数。"""
    reg, _ = _registry(tmp_path)
    params = reg.get("sc_wnn").parameters
    assert params["required"] == ["rna_file", "adt_file"]


def test_sc_knockout_forwards_params(tmp_path):
    """knockout 转发 gko 与网络参数。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "gko": "SPI1",
        "top_dr_genes": ["CTSS", "LYZ"], "n_genes": 1000}
    out = reg.get("sc_knockout").handler(
        dataset_ref="d", gko="SPI1", celltype_col="leiden", group="2",
        n_genes=800, n_net=5)
    args = runner.run.call_args
    assert args.args[0] == "knockout"
    assert args.args[1]["gko"] == "SPI1"
    assert args.args[1]["n_net"] == 5
    assert args.args[1]["group"] == "2"
    assert args.kwargs["timeout_sec"] == 3600
    assert out["top_dr_genes"] == ["CTSS", "LYZ"] and "ok" not in out


def test_sc_knockout_error_passthrough(tmp_path):
    """R 侧失败透传错误码。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "RuntimeError: Rscript knk.R failed")
    out = reg.get("sc_knockout").handler(dataset_ref="d", gko="XX")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
```

注意：wnn 单测 mock 了 `runner.resolve_data_path`/`runner.run`，但 `compute_dataset_id` 是模块级 import 的真实函数、要求文件存在——测试里 rna.h5ad/adt.h5ad 并不存在，会抛 SC_FILE_NOT_FOUND。处理：用 `unittest.mock.patch` 打 `orchestrator.tools.builtin.l3_singlecell.compute_dataset_id` 返回固定值（`patch(..., side_effect=["abcdef123456", "789012fedcba"]` 或 return_value）。逐字执行前先读 l3_singlecell.py 确认 compute_dataset_id 的 import 形态（`from ... import compute_dataset_id` → patch 目标为 l3_singlecell 命名空间），把 patch 加进用例 2/3（含 import `from unittest.mock import patch`）。

- [ ] **Step 2: 跑测试 + ruff**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_l3_singlecell.py -q --basetemp=.pytest_tmp`
Expected: `53 passed`（47 + 6）

Run: `.\.venv\Scripts\python.exe -m ruff check tests/unit/test_l3_singlecell.py`
Expected: 零告警

---

### Task 5: Dockerfile muon 层 + R 层

**Files:**
- Modify: `sandbox/bio.Dockerfile`（pyscenic 层后追加两层；COPY r_tools 加一行）

- [ ] **Step 1: 追加**

```dockerfile
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
```

并在现有 `COPY sc_tools/ /opt/sc_tools/` 行之后加：

```dockerfile
COPY r_tools/ /opt/r_tools/
```

- [ ] **Step 2: 宿主 muon 持久性确认（probe 已装，幂等核验）**

Run: `.\.venv\Scripts\python.exe -c "import muon, mudata; print(muon.__version__, mudata.__version__)"`
Expected: `0.1.9 0.4.1`

---

### Task 6: 本机冒烟（wnn 宿主）

**Files:**
- Create: `code_workspace/smoke_phase37.py`（untracked）

- [ ] **Step 1: 写冒烟脚本**

```python
"""Phase 37 wnn.py 本机冒烟（宿主 venv，muon 0.1.9）。

假数据：240 细胞；RNA 60 基因 3 簇结构 + ADT 8 蛋白（簇相关）。
写 rna.h5ad/adt.h5ad 到 DATA 目录，monkeypatch 后跑 wnn.py。
断言：新数据集 processed.h5ad/raw.h5ad 生成；leiden ARI>0.8；
wnn_weight 两列存在且每细胞和≈1；obsm X_umap 形状正确。
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"i:\飞书agent\sandbox\sc_tools")
WS = Path(r"i:\飞书agent\code_workspace\smoke_ws37")
DATA = Path(r"i:\飞书agent\code_workspace\smoke_data37")
WS.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(7)
n = 240
labels = np.repeat(["0", "1", "2"], 80)
rna_genes = [f"R{i:03d}" for i in range(60)]
prots = [f"ADT_{i}" for i in range(8)]
X_rna = rng.poisson(1.5, size=(n, 60)).astype(float)
X_adt = rng.poisson(0.8, size=(n, 8)).astype(float)
for k, lab in enumerate(["0", "1", "2"]):
    m = labels == lab
    X_rna[m, k * 5:(k + 1) * 5] += rng.poisson(6, (m.sum(), 5))
    X_adt[m, k * 2:(k + 1) * 2] += rng.poisson(4, (m.sum(), 2))

import anndata as ad

cells = [f"c{i}" for i in range(n)]
ad.AnnData(X=X_rna, obs=pd.DataFrame(index=cells),
           var=pd.DataFrame(index=rna_genes)).write(DATA / "rna37.h5ad")
ad.AnnData(X=X_adt, obs=pd.DataFrame(index=cells),
           var=pd.DataFrame(index=prots)).write(DATA / "adt37.h5ad")


def run_wnn(payload: dict) -> dict:
    """monkeypatch WS_ROOT/DATA_ROOT 后子进程跑 wnn.py。"""
    driver = (
        "import sys, pathlib; "
        "sys.path.insert(0, r'%s'); "
        "import common; "
        "common.WS_ROOT = pathlib.Path(r'%s'); "
        "common.DATA_ROOT = pathlib.Path(r'%s'); "
        "import wnn as m; m.WS_ROOT = common.WS_ROOT; "
        "m.DATA_ROOT = common.DATA_ROOT; m.run(m.main)"
        % (ROOT, WS, DATA))
    proc = subprocess.run(
        [sys.executable, "-c", driver], input=json.dumps(payload),
        capture_output=True, text=True, timeout=1200)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"], out
    return out


r = run_wnn({"dataset_id": "wnn37abc123", "rna_path": "rna37.h5ad",
             "adt_path": "adt37.h5ad", "rna_dims": 20, "adt_dims": 7,
             "seed": 42})
print("[wnn]", {k: r[k] for k in ("n_cells", "n_genes", "n_proteins",
                                  "n_clusters", "mean_modality_weights")})
import anndata as ad2
out = ad2.read_h5ad(WS / "wnn37abc123" / "processed.h5ad")
assert out.raw is not None and "X_umap" in out.obsm
assert {"wnn_weight_rna", "wnn_weight_adt"} <= set(out.obs.columns)
wsum = (out.obs["wnn_weight_rna"] + out.obs["wnn_weight_adt"]).to_numpy()
assert np.allclose(wsum, 1.0, atol=1e-3)
# ARI：leiden vs 注入簇
from sklearn.metrics import adjusted_rand_score
ari = adjusted_rand_score(labels, out.obs["leiden"].astype(str))
assert ari > 0.8, f"ARI={ari}"
print(f"[ok] ARI={ari:.3f}, weights sum ok, raw present")
print("SMOKE PASS: phase37 wnn end-to-end")
```

- [ ] **Step 2: 跑冒烟**

Run: `.\.venv\Scripts\python.exe code_workspace\smoke_phase37.py`
Expected: `[wnn]` 统计行 + `[ok] ARI=...` + `SMOKE PASS`；失败按 stderr 最小化修复并同步 sandbox 脚本，报告改动

---

### Task 7: docker 重建 + 断网验证 + knockout 容器冒烟

- [ ] **Step 1: 重建镜像**

Run: `docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile`
Expected: 成功（R 层 10-30 分钟：apt r-base + P3M/源码装 scTenifoldKnk 链；层内 library() 自检会立即暴露安装失败）；记录镜像 ID

- [ ] **Step 2: 断网容器 import 验证**

Run: `docker run --rm --network none feishu-research-agent/bio:cpu-latest bash -c "python -c \"import sys; sys.path.insert(0,'/opt/sc_tools'); import muon, common, wnn, knockout; print('py ok', muon.__version__)\" && Rscript -e \"suppressMessages(library(scTenifoldKnk)); cat('R ok', as.character(packageVersion('scTenifoldKnk')), '\n')\""`
Expected: `py ok 0.1.9` + `R ok 1.1`

- [ ] **Step 3: knockout 容器冒烟（R 链路集成终验）**

先造容器用假数据（宿主跑一段 python 生成 WS）：

```python
# code_workspace/mk_smoke37_data.py（untracked）
# 400 细胞 × 320 基因：TF "SPI1" 驱动 9 靶基因模块；写 filtered.h5ad(counts)
# + processed.h5ad(leiden,3 簇)。counts 规模保证 library size>500。
```

再断网容器跑：
```powershell
'{\"dataset_id\": \"ko37\", \"gko\": \"SPI1\", \"n_genes\": 300, \"n_net\": 5, \"n_cells\": 300, \"min_lib_size\": 200}' | docker run --rm -i --network none --cpus 4 --memory 16g -v i:\飞书agent\code_workspace\smoke_ws37ko:/ws feishu-research-agent/bio:cpu-latest python /opt/sc_tools/knockout.py
```
Expected: 末行 JSON `ok: true`，top_dr_genes 含注入模块基因（断言：top-20 ∩ 注入靶基因 ≥3——由 Step 3 驱动脚本检查 dr_csv 完成，容器内直接跑也行）；**注意 R 侧耗时应 <30 分钟**（300 基因 × 5 网 × 300 细胞规模），超时报全文

- [ ] **Step 4: 产物确认**：diffRegulation.csv + knockout_volcano.png 在 smoke_ws37ko/ko37/knockout/SPI1/ 下，时间戳为本次运行

---

### Task 8: 全量回归

Run: `.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp > .pytest_last.log 2>&1; Select-String -Path .pytest_last.log -Pattern '\d+ passed' | Select-Object -Last 1`
Expected: `1051 passed`（1045 + 6），0 failed

---

### Task 9: 文档同步 + GHIST 暂缓记录 + commit + 服务重启

**Files:**
- Modify: `docs/ROADMAP.md`（+Phase 37 行 + GHIST 暂缓立项条目：依赖 GPU 镜像 + Xenium 数据 + 核分割预处理链，数据齐备后再立）
- Modify: `docs/平台功能说明书.md`（6.1 节 21→23 工具；表格 +2 行）
- Modify: `docs/使用说明书.md`（场景 D 产物表 +2 行；话术："RNA 和蛋白数据整合分析"/"模拟敲掉某个基因看影响"）
- Modify: `测试总结+2026-09-06T11-41-23.md`（probe 已建；追加 Phase 37 实施节：muon 堆腐化坑防御、R 层 P3M/源码策略、冒烟结果、镜像 ID、回归统计、统一测试轮待办 18 工具、GHIST 暂缓说明）
- Modify: 本计划「验证记录」节回填

- [ ] **Step 1: 五文档同步**（范式仿 Phase 36）

- [ ] **Step 2: commit**

```powershell
git add sandbox/sc_tools/wnn.py sandbox/sc_tools/knockout.py sandbox/r_tools/knk.R sandbox/bio.Dockerfile orchestrator/tools/builtin/l3_singlecell.py tests/unit/test_l3_singlecell.py docs/superpowers/plans/2026-09-06-phase37-wnn-knockout.md docs/ROADMAP.md "docs/使用说明书.md" "docs/平台功能说明书.md" "测试总结+2026-09-06T11-41-23.md"
git commit -m "feat: Phase 37 WNN 多组学 + 虚拟敲除（toolsv1 第七批）——sc_wnn（muon 0.1.9，双文件白名单挂载+幂等双 hash 新 ref，n_multineighbors<n_obs 堆腐化防御，raw=RNA lognorm 新 dataset_ref 接入全生态）+ sc_knockout（scTenifoldKnk 1.1 R 容器保真链路：dense CSV→Rscript knk.R→diffRegulation 火山图，Debian trixie r-base 4.5 + P3M 二进制优先/源码回退同层 purge 工具链）；GHIST 暂缓记 ROADMAP（GPU+Xenium 数据齐备后立项）；wnn 宿主冒烟 ARI>0.8 + knockout 断网容器 R 链路集成跑通；TDD 6 用例，回归 1051 全过；真机验收延后统一测试轮（累计 18 工具），双说明书/ROADMAP/测试总结同步"
```

- [ ] **Step 3: 服务重启对齐 HEAD**（同前：Stop-Process 旧 pid → start.ps1 → err.log 验 Lark connected）

---

## 验证记录

> 回填时间：2026-09-06（Task 9 Step 1 文档同步轮）

### Task 1: wnn.py 容器脚本
- Step 1：✅ 已建 `sandbox/sc_tools/wnn.py`（含探针坑防御：`n_multineighbors=min(200,n_obs-1)` 防堆腐化；各模态先 `sc.pp.neighbors`；PCA n_comps 钳制）
- Step 2：✅ ruff 零告警（实施期修两处：删冗余 import；`_read` 加容器绝对路径容忍分支 `if rel.startswith("/")`，对齐 Task 3 异根双挂 `/data_adt/<rel>` 传参）

### Task 2: knockout.py + knk.R
- Step 1：✅ 已建 `sandbox/sc_tools/knockout.py`
- Step 2：✅ 已建 `sandbox/r_tools/knk.R`（**实施期修复**：参数名对齐 CRAN 1.1 实测签名——`qc_mtThreshold`→`qc_maxMTratio`、`qc_minLSize`→`qc_minLibSize`，容器内 `args(scTenifoldKnk)` 实证）
- Step 3：✅ ruff 零告警

### Task 3: 注册 2 个 ToolSpec + handler
- Step 1：✅ docstring `Phase 20/…/37`、`21 个`→`23 个`
- Step 2：✅ 两 handler 追加（**实施期修复**：删 ruff F841 冗余 `mounts` 预赋值；同根单挂 /data、异根双挂 /data+/data_adt 分支验证通过）
- Step 3：✅ 两 ToolSpec 追加（L1_compute，timeout 1200/3600）
- Step 4：✅ ruff 零告警

### Task 4: 单元测试（6 用例）
- Step 1：✅ 6 用例追加（注册/超时、同根单挂+双 hash dataset_id、异根双挂+容器绝对路径、schema required、knockout 参数转发、R 错误透传；按计划注意项 patch 了 `compute_dataset_id`）
- Step 2：✅ **53 passed**（47+6）；ruff 零告警

### Task 5: Dockerfile muon 层 + R 层
- Step 1：✅ muon 层（清华源）+ R 层（trixie apt r-base 4.5.0 + P3M trixie 装 scTenifoldKnk 1.1，同层 purge r-base-dev/g++）+ `COPY r_tools/ /opt/r_tools/`
- Step 2：✅ 宿主 `0.1.9 0.4.1` 幂等核验通过

### Task 6: 本机冒烟（wnn 宿主）
- Step 1：✅ `code_workspace/smoke_phase37.py`（untracked）
- Step 2：✅ **一次通过**：240 细胞，leiden vs 注入簇 **ARI=1.000**；wnn_weight 两列每细胞和=1；**ADT 权重 0.682 > RNA 0.318**（符合假数据信噪比设计）；新数据集 processed.h5ad（raw=RNA lognorm+X_umap+leiden+权重列）/raw.h5ad 齐备

### Task 7: docker 重建 + 断网验证 + knockout 容器冒烟
- Step 1：✅ 镜像 **bff5e3bf62b2**；R 层构建仅 **67.9s**（P3M trixie 二进制全命中，探针遗留问题闭环）
- Step 2：✅ 断网容器：`py ok 0.1.9` + `R ok 1.1`
- Step 3：✅ knockout 断网容器冒烟 **86 秒**跑通（400 细胞 × 320 基因，n_genes=300/n_net=5/n_cells=300）；diffRegulation **top-20 回收注入靶基因 7/9**（≥3 达标），**SPI1 自身 Z 值第一**
- Step 4：✅ 产物确认：diffRegulation.csv + knockout_volcano.png 落 smoke_ws37ko/ko37/knockout/SPI1/，时间戳本次运行

### Task 8: 全量回归
- ✅ **1051 passed**（1045+6），0 failed，57.39s（落 `.pytest_last.log`）

### Task 9: 文档同步 + GHIST 暂缓记录 + commit + 服务重启
- Step 1：✅ 五文档同步完成（ROADMAP Phase 37 行 + GHIST 远期池条目 / 平台功能说明书 6.1 节 21→23 + 表格 2 行 / 使用说明书场景 D 产物表 2 行 + 双话术 / 测试总结 Phase 37 实施节 / 本节回填）
- Step 2：commit——**7d5ab79**
- Step 3：服务重启——**待回填**（随 Step 2 一并执行）

### GHIST 决策记录
toolsv1 GHIST 实现为 PyTorch UNet3+ 从零训练框架（Xenium 级输入 5 类文件 + 核分割预处理链 + 无预训练权重 + 建议 24GB 显存），属独立赛道投入，**暂缓**：已记 ROADMAP 远期池「待 GPU + Xenium 数据齐备后立项」。

### 真机验收
延后统一测试轮（Phase 31-37 累计 18 工具，用户决策）。


