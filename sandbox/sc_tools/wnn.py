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

from typing import Any

from common import DATA_ROOT, WS_ROOT, emit, read_args, run


def _read(rel: str) -> Any:
    """从 /data 挂载读 h5ad（resolve_data_path 白名单已在 handler 层把关）。

    容忍以 "/" 开头的容器绝对路径（异根挂载时 ADT 走 /data_adt/...），
    该路径不校验 DATA_ROOT，直接读。
    """
    import anndata as ad

    if rel.startswith("/"):
        return ad.read_h5ad(rel)
    p = (DATA_ROOT / rel).resolve()
    if DATA_ROOT not in p.parents:
        raise ValueError(f"invalid path: {rel!r}")
    return ad.read_h5ad(p)


def _prep_rna(adata: Any, dims: int) -> tuple[Any, Any]:
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


def _prep_adt(adata: Any, dims: int) -> Any:
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
