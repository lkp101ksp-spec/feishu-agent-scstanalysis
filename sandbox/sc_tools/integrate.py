"""sc_integrate：批次整合（Phase 33 bbknn / Phase 69 harmony 双引擎）。

stdin: {"dataset_id": ..., "batch": "sample", "method": "bbknn|harmony",
        "n_top_hvg": 2000, "n_pcs": 50, "n_neighbors": 15, "resolution": 1.0}
input 链与 sc_process 一致（filtered.h5ad → raw.h5ad 默认过滤）。
bbknn：normalize→HVG→scale→PCA→bbknn 批次感知邻居→UMAP→Leiden。
harmony：同预处理→PCA→直调 harmonypy run_harmony 校正 PC
（X_pca_harmony，形状自适应 0.4/2.x）→按校正表示建邻居→UMAP→Leiden
（spec 2026-09-19-st-integrate-design.md）。
产物落 WS/{new_id}/processed.h5ad（new_id = {父id}_{method}），
返回新 dataset_ref，下游工具零改动可链。
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
from common import WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata: Any) -> str:
    """列出可作 batch 的 obs 列（2..50 个取值），错误消息引导自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def main() -> None:
    """主流程：预处理到 PCA → 按引擎去批次（bbknn/harmony）→ UMAP/Leiden。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    batch = str(args.get("batch", "")).strip()
    if not batch:
        raise ValueError("batch column is required, e.g. 'sample' or 'batch'")
    method = str(args.get("method", "bbknn")).strip().lower()
    if method not in ("bbknn", "harmony"):
        raise ValueError(
            f"unknown method {method!r}; expected 'bbknn' or 'harmony'")
    n_top_hvg = int(args.get("n_top_hvg", 2000))
    n_pcs = int(args.get("n_pcs", 50))
    n_neighbors = int(args.get("n_neighbors", 15))
    resolution = float(args.get("resolution", 1.0))

    parent = args["dataset_id"]
    adata = load_adata({"dataset_id": parent, "file": "any"})
    if batch not in adata.obs:
        raise ValueError(
            f"batch column {batch!r} not in obs; available: "
            f"{_cat_cols(adata)}")
    batches = adata.obs[batch].astype(str)
    n_batch = batches.nunique()
    if n_batch < 2:
        raise ValueError(
            f"only one batch value {sorted(batches.unique())[:5]}; "
            "nothing to integrate")

    if not (WS_ROOT / parent / "filtered.h5ad").exists():
        import scanpy as sc_pp  # noqa: F811 —— 默认过滤同 sc_process
        sc_pp.pp.filter_cells(adata, min_genes=600)
        sc_pp.pp.filter_genes(adata, min_cells=3)
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top_hvg,
                                flavor="seurat")
    adata.raw = adata
    adata = adata[:, adata.var["highly_variable"]].copy()
    sc.pp.scale(adata, max_value=10)
    n_comps = min(n_pcs, adata.n_vars - 1, adata.n_obs - 1)
    sc.tl.pca(adata, n_comps=n_comps, svd_solver="arpack")

    # 双引擎分支：产出均收敛到 adata 上的 neighbors 图（下游 UMAP/Leiden
    # 共用）；engine_out 承载各引擎 emit 差异键（bbknn 口径逐字节不变）。
    engine_out: dict[str, Any] = {}
    if method == "bbknn":
        try:
            import bbknn
        except ImportError as e:
            raise RuntimeError(
                "bbknn not installed in image; rebuild bio image with "
                "bbknn pip layer") from e
        # bbknn：每批取 n_neighbors/批数 个邻居（保总量近似，防小批被淹没）
        nwb = max(3, round(n_neighbors / n_batch))
        bbknn.bbknn(adata, batch_key=batch, neighbors_within_batch=nwb)
        engine_out["neighbors_within_batch"] = nwb
    else:
        try:
            import harmonypy
        except ImportError as e:
            raise RuntimeError(
                "harmonypy not installed in image; rebuild bio image with "
                "harmonypy pip layer") from e
        # 直调 harmonypy（scanpy 1.12 的 harmony_integrate 包装未适配
        # 2.x 的不转置约定，实测写 obsm 形状错）；Z_corr 按行数对齐
        # n_obs——2.x 为 (cells, pcs)，0.4.x 为 (pcs, cells) 需转置
        ho = harmonypy.run_harmony(
            np.asarray(adata.obsm["X_pca"], dtype=np.float64),
            adata.obs, batch, verbose=False)
        z = np.asarray(ho.Z_corr, dtype=np.float64)
        if z.shape[0] != adata.n_obs:
            z = z.T
        adata.obsm["X_pca_harmony"] = z
        sc.pp.neighbors(adata, n_neighbors=n_neighbors,
                        use_rep="X_pca_harmony")
        engine_out["representation"] = "X_pca_harmony"
    sc.tl.umap(adata)
    sc.tl.leiden(adata, resolution=resolution, flavor="igraph",
                 n_iterations=2, directed=False)

    new_id = re.sub(r"\W+", "_", f"{parent}_{method}")
    ds_dir = WS_ROOT / new_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    # UMAP 图：左整合后着色 batch，右着色 leiden（直观评估去批次）
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), dpi=150)
    sc.pl.umap(adata, color=batch, ax=axes[0], show=False,
               title=f"Integrated UMAP ({batch})")
    sc.pl.umap(adata, color="leiden", ax=axes[1], show=False,
               legend_loc="on data", title=f"Integrated UMAP (leiden, "
                                           f"res={resolution})")
    umap_png = ds_dir / "umap_integrated.png"
    fig.savefig(umap_png, bbox_inches="tight")
    plt.close(fig)

    adata.write_h5ad(ds_dir / "processed.h5ad")
    sizes = adata.obs["leiden"].value_counts().to_dict()
    emit({
        "ok": True,
        "dataset_ref": new_id,
        "parent_ref": parent,
        "method": method,
        "batch": batch,
        "n_batches": int(n_batch),
        **engine_out,
        "n_cells": int(adata.n_obs),
        "n_clusters": int(adata.obs["leiden"].nunique()),
        "cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        "umap_png": str(umap_png),
        "note": ("downstream sc_* tools accept this new dataset_ref "
                 "directly"),
    })


if __name__ == "__main__":
    run(main)
