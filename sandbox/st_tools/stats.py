"""st_stats 空间统计三分析（Phase 45）：autocorr / cooccurrence /
nhood_enrichment。邻域图优先复用 st_process 已建 spatial_connectivities，
缺失时按 coord_type/n_neighs 兜底补建（processed.h5ad 只读不写回）。
产物：csv 落 /ws/{ds}/stats_{analysis}/，图走 pngs 聚合键（宿主收图口径）。

squidpy 1.8.3 容器探针已核实：
- gr.spatial_autocorr(mode, genes, show_progress_bar) → uns["moranI"]
  列 I/pval_norm/var_norm/pval_norm_fdr_bh（gearyC 同构，统计列 C）
- gr.co_occurrence(cluster_key) → uns[f"{k}_co_occurrence"] 键为
  occ（shape n_clusters×n_clusters×n_bins，索引 prob[i, j, d_idx]）
  与 interval（距离边界数组）
- gr.nhood_enrichment(cluster_key, n_perms, show_progress_bar)
  → uns[f"{k}_nhood_enrichment"] 键 zscore/count（n×n 方阵）
- pl.spatial_scatter 需 return_ax=True（st_domains T7 教训）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common import emit, fail, run

ANALYSES = ("autocorr", "cooccurrence", "nhood_enrichment")
CLUSTER_FALLBACK = ("spatial_domain", "banksy_domain", "leiden", "clusters")
AUTOCORR_DEFAULT_N_GENES = 50


def _build_neighbors(adata: Any, coord_type: str, n_neighs: int) -> None:
    """复用 st_process 已建邻域图；缺失才按参数补建（不写回）。"""
    if "spatial_connectivities" in adata.obsp:
        return
    import squidpy as sq
    if coord_type == "generic":
        sq.gr.spatial_neighbors(adata, coord_type="generic", delaunay=True)
    else:
        sq.gr.spatial_neighbors(adata, coord_type="grid", n_neighs=n_neighs)


def _resolve_cluster_key(adata: Any, cluster_key: str) -> str:
    """显式列名优先；留空按回退链探测；全灭报错列可用 obs 列。"""
    if cluster_key:
        if cluster_key not in adata.obs.columns:
            fail("ST_CLUSTER_KEY_MISSING",
                 f"obs 无列 '{cluster_key}'；可用: "
                 f"{list(adata.obs.columns)}")
            raise SystemExit(1)
        return cluster_key
    for cand in CLUSTER_FALLBACK:
        if cand in adata.obs.columns:
            return cand
    fail("ST_CLUSTER_KEY_MISSING",
         f"无可用簇列（回退链 {CLUSTER_FALLBACK} 全缺失）；"
         f"可用 obs 列: {list(adata.obs.columns)}")
    raise SystemExit(1)


def _scatter_img_kwargs(adata: Any) -> dict[str, Any]:
    """spatial_scatter 图像参数：无 uns['spatial']（h5ad 来源合成/外部
    数据）补空壳 + img=False；有真实组织图（visium 加载）则默认带图。

    容器探针（2026-09-11）：uns 缺 'spatial' → KeyError；空 images 壳
    不指定 img=False → 仍尝试取 hires 报错；两坑同避。
    """
    sp = adata.uns.get("spatial")
    if not isinstance(sp, dict) or not sp:
        import numpy as np
        adata.uns["spatial"] = {"_placeholder": {
            "images": {"hires": np.zeros((8, 8, 3))},
            "scalefactors": {"tissue_hires_scalef": 1.0,
                             "spot_diameter_fullres": 1.0}}}
        return {"img": False}
    has_img = any(isinstance(lib, dict) and lib.get("images")
                  for lib in sp.values())
    return {} if has_img else {"img": False}


def _do_autocorr(adata: Any, out_dir: Path, mode: str,
                 genes: list[str]) -> None:
    """Moran's I / Geary's C：逐基因统计 csv + top4 空间分布 png。"""
    import matplotlib.pyplot as plt
    import squidpy as sq

    if not genes:
        if "highly_variable" in adata.var.columns:
            genes = list(adata.var_names[adata.var["highly_variable"]])[
                :AUTOCORR_DEFAULT_N_GENES]
        else:
            genes = list(adata.var_names)[:AUTOCORR_DEFAULT_N_GENES]
    else:
        missing = [g for g in genes if g not in adata.var_names]
        if missing:
            fail("INVALID_INPUT",
                 f"基因不在 var_names: {missing[:5]}（共 {len(missing)} 个）")
            raise SystemExit(1)
    sq.gr.spatial_autocorr(adata, mode=mode, genes=genes,
                           show_progress_bar=False)
    df = adata.uns["moranI" if mode == "moran" else "gearyC"]
    df = df.reset_index().rename(columns={"index": "gene"})
    stat_col = "I" if mode == "moran" else "C"
    df = df.sort_values(stat_col, ascending=(mode != "moran"))
    df.to_csv(out_dir / f"autocorr_{mode}.csv", index=False)
    top = df.iloc[0]
    top_genes = list(df["gene"].head(4))
    img_kw = _scatter_img_kwargs(adata)
    for g in top_genes:
        stat = float(df.loc[df["gene"] == g, stat_col].iloc[0])
        ax = sq.pl.spatial_scatter(adata, color=[g], return_ax=True,
                                   **img_kw)
        ax.set_title(f"{g} ({stat_col}={stat:.3f})")
        ax.figure.savefig(out_dir / f"autocorr_{g}.png", dpi=150,
                          bbox_inches="tight")
        plt.close("all")
    emit({"ok": True, "analysis": "autocorr", "mode": mode,
          "n_genes_tested": len(genes),
          "top_gene": str(top["gene"]), "top_stat": float(top[stat_col]),
          "stats_csv": f"{out_dir}/autocorr_{mode}.csv",
          "pngs": [f"{out_dir}/autocorr_{g}.png" for g in top_genes]})


def _do_cooccurrence(adata: Any, out_dir: Path, cluster_key: str) -> None:
    """簇间空间共现：长表 csv + 共现曲线 png。"""
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import squidpy as sq

    sq.gr.co_occurrence(adata, cluster_key=cluster_key)
    occ = adata.uns[f"{cluster_key}_co_occurrence"]
    prob = occ["occ"]  # (n_clusters, n_clusters, n_bins)
    interval = np.asarray(occ["interval"])
    clusters = list(adata.obs[cluster_key].cat.categories)
    rows = [{"cluster": c, "neighbor_cluster": clusters[j],
             "dist": float(interval[d]), "prob": float(prob[i, j, d])}
            for i, c in enumerate(clusters)
            for j in range(len(clusters))
            for d in range(prob.shape[2])]
    pd.DataFrame(rows).to_csv(out_dir / "cooccurrence.csv", index=False)
    sq.pl.co_occurrence(adata, cluster_key=cluster_key,
                        clusters=clusters[: min(6, len(clusters))],
                        figsize=(8, 6))
    plt.gcf().savefig(out_dir / "cooccurrence.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")
    emit({"ok": True, "analysis": "cooccurrence",
          "cluster_key": cluster_key, "n_clusters": len(clusters),
          "cooccurrence_csv": f"{out_dir}/cooccurrence.csv",
          "pngs": [f"{out_dir}/cooccurrence.png"]})


def _do_nhood(adata: Any, out_dir: Path, cluster_key: str,
              n_perms: int) -> None:
    """邻域富集：zscore/count 双矩阵 csv + 热图 png。"""
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import squidpy as sq

    sq.gr.nhood_enrichment(adata, cluster_key=cluster_key, n_perms=n_perms,
                           show_progress_bar=False)
    enr = adata.uns[f"{cluster_key}_nhood_enrichment"]
    z = np.asarray(enr["zscore"], dtype=float)
    cnt = np.asarray(enr["count"], dtype=float)
    cats = list(adata.obs[cluster_key].cat.categories)
    pd.DataFrame(z, index=cats, columns=cats).to_csv(
        out_dir / "nhood_zscore.csv")
    pd.DataFrame(cnt, index=cats, columns=cats).to_csv(
        out_dir / "nhood_count.csv")
    mask = ~np.eye(len(cats), dtype=bool)
    z_off = np.where(mask, z, np.nan)
    # 取 |z| 最强非对角对：分离结构中强耗竭（负 z）本身就是核心信号
    i, j = np.unravel_index(np.nanargmax(np.abs(z_off)), z.shape)
    sq.pl.nhood_enrichment(adata, cluster_key=cluster_key, method="ward",
                           figsize=(8, 6))
    plt.gcf().savefig(out_dir / "nhood_enrichment.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")
    emit({"ok": True, "analysis": "nhood_enrichment",
          "cluster_key": cluster_key, "n_perms": n_perms,
          "top_pair": f"{cats[i]}~{cats[j]}",
          "top_zscore": float(z[i, j]),
          "zscore_csv": f"{out_dir}/nhood_zscore.csv",
          "count_csv": f"{out_dir}/nhood_count.csv",
          "pngs": [f"{out_dir}/nhood_enrichment.png"]})


def main() -> None:
    """st_stats 容器入口：三分析分发。"""
    from common import WS_ROOT, ensure_spatial, load_adata, read_args

    args = read_args()
    analysis = str(args.get("analysis", ""))
    if analysis not in ANALYSES:
        fail("INVALID_INPUT",
             f"analysis 须为 {ANALYSES} 之一，收到 '{analysis}'")
        raise SystemExit(1)
    mode = str(args.get("mode", "moran"))
    if mode not in ("moran", "geary"):
        fail("INVALID_INPUT", f"mode 须为 moran/geary，收到 '{mode}'")
        raise SystemExit(1)
    genes = args.get("genes") or []
    if isinstance(genes, str):
        genes = [genes]
    n_perms = int(args.get("n_perms", 1000))
    coord_type = str(args.get("coord_type", "grid"))
    if coord_type not in ("grid", "generic"):
        fail("INVALID_INPUT",
             f"coord_type 须为 grid/generic，收到 '{coord_type}'")
        raise SystemExit(1)
    n_neighs = int(args.get("n_neighs", 6))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    _build_neighbors(adata, coord_type, n_neighs)

    out_dir = WS_ROOT / args["dataset_id"] / f"stats_{analysis}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if analysis == "autocorr":
        _do_autocorr(adata, out_dir, mode, list(genes))
    elif analysis == "cooccurrence":
        _do_cooccurrence(adata, out_dir, _resolve_cluster_key(
            adata, str(args.get("cluster_key", ""))))
    else:
        _do_nhood(adata, out_dir, _resolve_cluster_key(
            adata, str(args.get("cluster_key", ""))), n_perms)


if __name__ == "__main__":
    run(main)
