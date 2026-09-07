"""sc_process：归一化→HVG→scale→PCA→邻居→UMAP→Leiden（Phase 20/25）。

stdin: {"dataset_id": ..., "n_top_hvg": 2000, "n_pcs": 50,
        "n_neighbors": 15, "resolution": 1.0}
产出 processed.h5ad + umap.png；无 filtered.h5ad 时用 raw.h5ad 内置默认过滤。
Phase 25：import 探测 rapids_singlecell——GPU 镜像走 rsc 加速分支
（PCA/neighbors/UMAP/leiden），CPU 镜像行为与 Phase 20 完全一致。
"""
from __future__ import annotations

from common import WS_ROOT, emit, load_adata, read_args, run


def main() -> None:
    """主流程：import 探测 rapids_singlecell 决定 GPU/CPU 分支。"""
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
        # leiden 回 CPU：WSL2 下 rsc.tl.leiden 构造 cudf.DataFrame 触发 RMM
        # pinned-host 池扩容（cudaHostAlloc 100MiB 失败，实测 59900 细胞
        # 2026-09-03）；graph 已由 GPU neighbors 产出，CPU igraph 数秒完成
        rsc.get.anndata_to_CPU(adata)
        sc.tl.leiden(adata, resolution=resolution, flavor="igraph",
                     n_iterations=2, directed=False)
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
