"""st_domains：空间域细分（banksy-lite / leiden）→ domains.h5ad + 对比图。

stdin: {"dataset_id": "...", "method": "banksy"|"leiden", "resolution": 1.0}
banksy-lite = 邻域均值特征拼接（BANKSY 论文均值项近似，λ=0.25）：
  X_banksy = [X_pca, λ · A_norm @ X_pca]，再标准 Leiden。
  （squidpy 1.8.3 无内置 banksy、PyPI 无该包，故手写均值项拼接——
  OSTA/BioC 教程认可的 Banksy 核心近似，零新依赖）
ARI = 与批① process 的 spatial_domain 的 adjusted_rand_score。
产出：banksy_domains.png（新域着色）+ compare_leiden.png（旧域对照）。
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, fail, load_adata, read_args

    args = read_args()
    method = str(args.get("method", "banksy")).lower()
    resolution = float(args.get("resolution", 1.0))
    if method not in ("banksy", "leiden"):
        fail("INVALID_INPUT", f"method must be banksy|leiden, got {method!r}")
        return

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    ensure_spatial(adata)

    import matplotlib.pyplot as plt
    import numpy as np
    import scanpy as sc
    import squidpy as sq
    from sklearn.metrics import adjusted_rand_score

    n_neighbors = 15
    if method == "banksy":
        lam = 0.25
        # 行归一化空间邻接矩阵 → 邻域均值 PCA 特征
        A = adata.obsp["spatial_connectivities"].astype(np.float64)
        A = A.multiply(1.0 / np.maximum(A.sum(axis=1), 1e-9)).tocsr()
        neigh_mean = np.asarray(A @ adata.obsm["X_pca"])
        adata.obsm["X_banksy"] = np.hstack(
            [adata.obsm["X_pca"], lam * neigh_mean])
        use_rep = "X_banksy"
    else:
        use_rep = "X_pca"
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, use_rep=use_rep)
    sc.tl.leiden(adata, resolution=resolution, key_added="banksy_domain")

    ari = float(adjusted_rand_score(
        adata.obs["spatial_domain"].astype(str).values,
        adata.obs["banksy_domain"].astype(str).values))
    sizes = adata.obs["banksy_domain"].value_counts().to_dict()

    ds_dir = WS_ROOT / args["dataset_id"]
    # squidpy>=1.8 spatial_scatter 需 return_ax=True（批① T7 教训）
    ax = sq.pl.spatial_scatter(adata, color="banksy_domain", return_ax=True)
    ax.figure.savefig(ds_dir / "banksy_domains.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")
    ax = sq.pl.spatial_scatter(adata, color="spatial_domain", return_ax=True)
    ax.figure.savefig(ds_dir / "compare_leiden.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")

    adata.write_h5ad(ds_dir / "domains.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        "n_domains": int(len(sizes)),
        "cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        "ari_vs_leiden": round(ari, 4),
        "spatial_png": f"/ws/{args['dataset_id']}/banksy_domains.png",
        "pngs": [f"/ws/{args['dataset_id']}/compare_leiden.png"],
    })


if __name__ == "__main__":
    run(main)
