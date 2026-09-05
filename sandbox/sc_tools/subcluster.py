"""sc_subcluster：亚聚类（Phase 33，对齐 toolsv1 server_subcluster_*）。

stdin: {"dataset_id": ..., "clusters": ["0", "1"],
        "n_top_hvg": 2000, "n_pcs": 50, "n_neighbors": 15, "resolution": 1.0}
需父 processed.h5ad。取指定簇子集，从 raw（归一化 log 全基因）重建矩阵
重跑 HVG→scale→PCA→邻居→UMAP→Leiden（标签重编 0..k）。
产物落 WS/{new_id}/processed.h5ad（new_id = {父id}_sub{簇号-连写}），
返回新 dataset_ref——markers/富集/打分/代谢/拟时序零改动可链，支持多级亚聚类。
"""
from __future__ import annotations

import re

from common import WS_ROOT, emit, load_adata, read_args, run

MIN_CELLS = 20


def main() -> None:
    """主流程：子集→raw 重建→重聚类→写新 dataset processed.h5ad。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    clusters = [str(c) for c in args.get("clusters") or []]
    if not clusters:
        raise ValueError("clusters is required, e.g. ['0', '1']")
    n_top_hvg = int(args.get("n_top_hvg", 2000))
    n_pcs = int(args.get("n_pcs", 50))
    n_neighbors = int(args.get("n_neighbors", 15))
    resolution = float(args.get("resolution", 1.0))

    parent = args["dataset_id"]
    adata = load_adata({"dataset_id": parent, "file": "processed"})
    if "leiden" not in adata.obs:
        raise ValueError("parent processed.h5ad lacks leiden; "
                         "run sc_process first")
    labels = adata.obs["leiden"].astype(str)
    have = set(labels.unique())
    missing = [c for c in clusters if c not in have]
    if missing:
        raise ValueError(
            f"clusters {missing} not in parent leiden; existing: "
            f"{sorted(have)[:30]}")

    # 从 raw 重建（归一化 log 全基因，与 process 口径一致）
    sub = adata.raw.to_adata()[labels.isin(clusters)].copy()
    if sub.n_obs < MIN_CELLS:
        raise ValueError(
            f"subset has only {sub.n_obs} cells (<{MIN_CELLS}); "
            "merge more clusters")
    sub.obs_names_make_unique()
    sc.pp.highly_variable_genes(sub, n_top_genes=min(n_top_hvg, sub.n_vars),
                                flavor="seurat")
    sub.raw = sub
    sub = sub[:, sub.var["highly_variable"]].copy()
    sc.pp.scale(sub, max_value=10)
    n_comps = min(n_pcs, sub.n_vars - 1, sub.n_obs - 1)
    sc.tl.pca(sub, n_comps=n_comps, svd_solver="arpack")
    sc.pp.neighbors(sub, n_neighbors=min(n_neighbors, sub.n_obs - 1))
    sc.tl.umap(sub)
    sc.tl.leiden(sub, resolution=resolution, flavor="igraph",
                 n_iterations=2, directed=False)

    new_id = re.sub(r"\W+", "_",
                    f"{parent}_sub{'-'.join(sorted(set(clusters)))}")
    ds_dir = WS_ROOT / new_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(7, 5), dpi=150)
    sc.pl.umap(sub, color="leiden", ax=ax, show=False, legend_loc="on data",
               title=f"Subcluster of {parent}: {sorted(set(clusters))}")
    umap_png = ds_dir / "umap.png"
    fig.savefig(umap_png, bbox_inches="tight")
    plt.close(fig)

    sub.write_h5ad(ds_dir / "processed.h5ad")
    sizes = sub.obs["leiden"].value_counts().to_dict()
    emit({
        "ok": True,
        "dataset_ref": new_id,
        "parent_ref": parent,
        "source_clusters": sorted(set(clusters)),
        "n_cells": int(sub.n_obs),
        "n_clusters": int(sub.obs["leiden"].nunique()),
        "cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        "umap_png": str(umap_png),
        "note": ("downstream sc_* tools accept this new dataset_ref "
                 "directly"),
    })


if __name__ == "__main__":
    run(main)
