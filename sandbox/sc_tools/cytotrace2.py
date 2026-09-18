"""sc_cytotrace2：CytoTRACE2 绝对干性打分（独立工具，测试总结六补记
死刑推翻翻案落地：Kang et al. Nature Methods 2025）。

stdin: {"dataset_id": ..., "species": "mouse",   # mouse 默认/human
        "ncores": 4,                              # R 侧并行核数
        "batch_size": 0,      # >0 显式传 R；0=包默认 10000
        "smooth_batch_size": 0,  # >0 显式传 R；0=包默认 1000
        "cluster_col": "leiden"}  # 簇汇总列（缺失则跳过汇总）

输入硬约束：整数 raw/CPM 计数（模型要求不能 log/scaled）。
定位链：filtered.h5ad → raw.h5ad（X 整数率抽查）→ processed.h5ad
（layers["counts"] → X → .raw 依次整数率抽查）；全不命中
INVALID_INPUT（提示用 sc_load/sc_qc 产物或 merge_10x 形态上游）。

主链：计数矩阵 genes×cells 稀疏 MatrixMarket 导出（integer field，
全程不 dense 化，内存口径 nnz×12B——大库适配，q3 探针实证 R 侧
cytotrace2() 直吃 dgCMatrix）→ Rscript
/opt/r_tools/cytotrace2_bridge.R（模型参数随包分发断网可用）→
读回 c2_result.csv 五列。簇汇总/UMAP 图取 processed.h5ad 的 obs
（条码对齐）与 X_umap，缺失跳过对应产物（打分主链不依赖）。

产物：cytotrace2_result.csv（cell×5 列）/ cytotrace2_by_cluster.csv
（簇 preKNN 均值+n 降序）/ cytotrace2_umap.png（Score 着色）；
五列写回 processed.h5ad（无 processed 则写回计数来源 h5ad）obs。
out：n_cells/n_genes_input/n_genes_mapped/species/potency_table/
top5_clusters（preKNN 簇均值降序前 5）。

容器探针（六补记）：10k 细胞断网 11.9min/4 核；大库纪律 subset
~1000（project_rules.md）适用于探索期反复验证，稀疏导出后 59900
级全量单次结论可行（R 侧内部按 batch_size 分批处理）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, read_args, run

R_BRIDGE = "/opt/r_tools/cytotrace2_bridge.R"
OUT_COLS = [
    "CytoTRACE2_Score",
    "CytoTRACE2_Potency",
    "CytoTRACE2_Relative",
    "preKNN_CytoTRACE2_Score",
    "preKNN_CytoTRACE2_Potency",
]


def _is_counts(x: Any, n_sample: int = 200) -> bool:
    """抽查前 n_sample 细胞：非负且整数率>0.99 判为计数形态。"""
    x = x[:n_sample]
    x = x.toarray() if hasattr(x, "toarray") else np.asarray(x)
    x = x.astype(np.float64, copy=False)
    return bool(x.size and np.all(x >= 0) and float(np.mean(x == np.round(x))) > 0.99)


def _locate_counts(ds_dir: Path) -> tuple[Path | None, str]:
    """按回退链找整数计数矩阵：filtered → raw → processed 三路。"""
    import anndata as ad

    for name in ("filtered.h5ad", "raw.h5ad"):
        p = ds_dir / name
        if p.exists():
            a = ad.read_h5ad(p)
            if _is_counts(a.X):
                return p, f"{name}:X"
    p = ds_dir / "processed.h5ad"
    if p.exists():
        a = ad.read_h5ad(p)
        if "counts" in a.layers and _is_counts(a.layers["counts"]):
            return p, "processed.h5ad:layers[counts]"
        if _is_counts(a.X):
            return p, "processed.h5ad:X"
        if a.raw is not None and _is_counts(a.raw.X):
            return p, "processed.h5ad:raw"
    return None, ""


def main() -> None:
    args = read_args()
    ds = str(args["dataset_id"])
    species = str(args.get("species", "mouse"))
    if species not in ("mouse", "human"):
        fail("INVALID_INPUT", "species must be 'mouse' or 'human'")
        return
    ncores = int(args.get("ncores", 4))
    batch_size = int(args.get("batch_size", 0))
    smooth_batch_size = int(args.get("smooth_batch_size", 0))
    cluster_col = str(args.get("cluster_col", "leiden"))
    ds_dir = WS_ROOT / ds

    src, src_desc = _locate_counts(ds_dir)
    if src is None:
        fail(
            "INVALID_INPUT",
            "no integer raw/CPM counts found under dataset "
            f"{ds!r} (checked filtered/raw.h5ad and processed "
            "counts layer/X/raw); CytoTRACE2 requires non-log "
            "raw or CPM/TPM counts — use sc_load/sc_qc products "
            "or merge_10x-style integer upstream",
        )
        return

    import anndata as ad

    a_src = ad.read_h5ad(src)
    x: Any
    if src_desc.endswith(":layers[counts]"):
        x = a_src.layers["counts"]
        genes = pd.Index(a_src.var_names.astype(str))
    elif src_desc.endswith(":raw"):
        x = a_src.raw.X
        genes = pd.Index(a_src.raw.var_names.astype(str))
    else:
        x = a_src.X
        genes = pd.Index(a_src.var_names.astype(str))
    cells = pd.Index(a_src.obs_names.astype(str))
    # 基因重名防御（模型要求无重复基因名）
    dup = genes.duplicated(keep="first")
    if dup.any():
        keep_mask = ~dup.to_numpy()
        x = x[:, keep_mask] if hasattr(x, "tocsr") else x[:, keep_mask]
        genes = genes[keep_mask]

    in_dir = ds_dir / "_c2_in"
    in_dir.mkdir(parents=True, exist_ok=True)
    # 稀疏导出（大库适配，q3 探针实证 R 侧吃 dgCMatrix）：全程
    # 不 dense 化——内存口径 nnz×12B，59900×32285 可控；dense tsv
    # 口径 cells×genes×8B 同量级会爆（工具注释钉注的旧限制解除）
    from scipy import sparse
    from scipy.io import mmwrite

    xs = x.T.tocsr() if sparse.issparse(x) else sparse.csr_matrix(np.asarray(x).T)
    xs.data = np.round(xs.data).astype(np.int64)  # 整数计数化
    expr_mtx = in_dir / "expr.mtx"
    mmwrite(expr_mtx, xs, field="integer")  # 整数计数 mtx
    genes.to_series().to_csv(in_dir / "genes.csv", index=False, header=False)
    cells.to_series().to_csv(in_dir / "barcodes.csv", index=False, header=False)
    del xs

    r_cmd = ["Rscript", R_BRIDGE, str(expr_mtx), str(in_dir), species, str(ncores)]
    if batch_size > 0:
        r_cmd.append(str(batch_size))
    elif smooth_batch_size > 0:
        r_cmd.append("")  # batch 占位空串=R 内部转 NULL
    if smooth_batch_size > 0:
        r_cmd.append(str(smooth_batch_size))
    proc = subprocess.run(r_cmd, capture_output=True, text=True, timeout=7200)  # 全量口径：10k≈12min 线性
    # 外推 59900≈70min，l3 层 1800s 超时截断的是子集默认路径；
    # 全量单次结论走容器直跑（无 l3 外层）
    if proc.returncode != 0:
        fail("SCRIPT_ERROR", f"Rscript cytotrace2_bridge.R failed: {proc.stderr[-1200:]}")
        return
    res_p = in_dir / "c2_result.csv"
    if not res_p.exists():
        fail(
            "SCRIPT_ERROR",
            f"cytotrace2_bridge.R did not produce c2_result.csv; stdout tail: {proc.stdout[-500:]}",
        )
        return

    res = pd.read_csv(res_p, index_col=0)
    res.index = res.index.astype(str)
    meta_p = in_dir / "c2_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}

    # 写回：processed 优先（obs/umap 所在）；无 processed 写回计数来源
    write_p = ds_dir / "processed.h5ad" if (ds_dir / "processed.h5ad").exists() else src
    aw = ad.read_h5ad(write_p)
    al = res.reindex(pd.Index(aw.obs_names.astype(str)))
    aw.obs["CytoTRACE2_Score"] = al["CytoTRACE2_Score"].to_numpy(dtype=float)
    aw.obs["CytoTRACE2_Relative"] = al["CytoTRACE2_Relative"].to_numpy(dtype=float)
    aw.obs["preKNN_CytoTRACE2_Score"] = al["preKNN_CytoTRACE2_Score"].to_numpy(dtype=float)
    aw.obs["CytoTRACE2_Potency"] = pd.Categorical(al["CytoTRACE2_Potency"].astype(str))
    aw.obs["preKNN_CytoTRACE2_Potency"] = pd.Categorical(al["preKNN_CytoTRACE2_Potency"].astype(str))

    # 簇汇总：preKNN 口径（FAQ：稀有表型 KNN 平滑拉偏，preKNN 更稳）
    by_csv = ""
    top5: dict[str, float] = {}
    if cluster_col in aw.obs:
        cl = aw.obs[cluster_col].astype(str)
        by = (
            pd.DataFrame({"cluster": cl, "preKNN": aw.obs["preKNN_CytoTRACE2_Score"].to_numpy()})
            .groupby("cluster")["preKNN"]
            .agg(["mean", "count"])
            .sort_values("mean", ascending=False)
        )
        by_csv = str(ds_dir / "cytotrace2_by_cluster.csv")
        by.round(4).to_csv(by_csv)
        top5 = {str(k): round(float(v), 4) for k, v in by["mean"].head(5).items()}

    # UMAP 着色（X_umap 缺失跳过）
    umap_png = ""
    if "X_umap" in aw.obsm:
        import matplotlib.pyplot as plt

        umap: np.ndarray = np.asarray(aw.obsm["X_umap"])
        sc = aw.obs["CytoTRACE2_Score"].to_numpy(dtype=float)
        fig, ax = plt.subplots(figsize=(5.6, 4.4))
        s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=sc, cmap="viridis", linewidths=0)
        fig.colorbar(s, ax=ax, fraction=0.046, label="CytoTRACE2 Score")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("CytoTRACE2 absolute potency", fontsize=9)
        fig.tight_layout()
        umap_png = str(ds_dir / "cytotrace2_umap.png")
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    aw.write_h5ad(write_p)
    res_csv = str(ds_dir / "cytotrace2_result.csv")
    res.to_csv(res_csv)

    potency_table = aw.obs["CytoTRACE2_Potency"].astype(str).value_counts().to_dict()
    emit(
        {
            "ok": True,
            "n_cells": int(aw.n_obs),
            "n_genes_input": int(meta.get("n_genes_input", len(genes))),
            "n_genes_mapped": int(meta.get("n_genes_mapped", -1)),
            "species": species,
            "counts_source": src_desc,
            "potency_table": potency_table,
            "top5_clusters": top5,
            "cytotrace2_result_csv": res_csv,
            "cytotrace2_by_cluster_csv": by_csv,
            "umap_png": umap_png,
        }
    )


run(main)
