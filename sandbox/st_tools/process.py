"""st_process：邻域图→PCA→Leiden 空间域 → processed.h5ad + 图（Phase 21）。

stdin: {"dataset_id": "...", "n_pcs": 30, "resolution": 1.0, "n_neighbors": 15}
产出：umap.png（域着色）+ spatial_domains.png（空间域着色，st 核心输出）。
空间域 = Leiden 聚类（PCA 表征 + 标准邻接图）；空间邻域图由
sq.gr.spatial_neighbors 构建（供下游 st_domains/commot 复用）。
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, load_adata, read_args

    args = read_args()
    n_pcs = int(args.get("n_pcs", 30))
    resolution = float(args.get("resolution", 1.0))
    n_neighbors = int(args.get("n_neighbors", 15))

    adata = load_adata({"dataset_id": args["dataset_id"]})
    ensure_spatial(adata)

    import matplotlib.pyplot as plt
    import scanpy as sc
    import squidpy as sq

    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=min(2000, adata.n_vars),
                                flavor="seurat")
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=min(n_pcs, adata.n_obs - 1, adata.n_vars - 1),
              svd_solver="arpack")

    # 空间邻域（visium 网格 → hex 邻接；sq 按坐标推断）——存入
    # obsp["spatial_connectivities"]，供下游 st_domains/st_commot 复用
    sq.gr.spatial_neighbors(adata, n_neighs=n_neighbors, coord_type="generic")
    # 空间域：PCA 表征上的标准邻接 Leiden
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, use_rep="X_pca")
    sc.tl.leiden(adata, resolution=resolution, key_added="spatial_domain")

    sc.tl.umap(adata)
    domain_sizes = adata.obs["spatial_domain"].value_counts().to_dict()

    ds_dir = WS_ROOT / args["dataset_id"]
    # squidpy>=1.8 spatial_scatter 无 show/return_fig 参数（透传会触发
    # PatchCollection.set() TypeError），需 return_ax=True 才返回 Axes
    ax = sq.pl.spatial_scatter(adata, color="spatial_domain", return_ax=True)
    ax.figure.savefig(ds_dir / "spatial_domains.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")

    fig = sc.pl.umap(adata, color="spatial_domain", show=False,
                     return_fig=True)
    fig.savefig(ds_dir / "umap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    adata.write_h5ad(ds_dir / "processed.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_domains": int(len(domain_sizes)),
        "cluster_sizes": {str(k): int(v) for k, v in domain_sizes.items()},
        "umap_png": f"/ws/{args['dataset_id']}/umap.png",
        "spatial_png": f"/ws/{args['dataset_id']}/spatial_domains.png",
    })


if __name__ == "__main__":
    run(main)
