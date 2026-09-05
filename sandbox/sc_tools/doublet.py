"""sc_doublet：双联体检测（Phase 35，scrublet；对齐 scop RunDoubletCalling）。

stdin: {"dataset_id": ..., "expected_rate": 0.06, "n_prin_comps": 30,
        "celltype_col": "leiden"}
scrublet 需整数 counts（processed 的 raw 是 log-norm 不可直接用）→
读 filtered/raw 回退链取 counts，按 obs_names 交集写回 processed.h5ad：
obs 增 doublet_score / predicted_doublet（只标记不删除，过滤走 sc_qc
语义）。自动阈值失败时回退 expected_rate 分位数阈值并在 note 说明。
产物：doublet_score UMAP（预测双联体黑圈）+ 各簇双联体率 csv。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def main() -> None:
    """主流程：counts 上跑 scrublet → 写回 processed → UMAP + 簇率 csv。"""
    import matplotlib.pyplot as plt
    import scrublet as scr

    args = read_args()
    expected_rate = float(args.get("expected_rate", 0.06))
    n_prin_comps = int(args.get("n_prin_comps", 30))
    celltype_col = str(args.get("celltype_col", "leiden")).strip()

    counts_ad = load_adata({"dataset_id": args["dataset_id"], "file": "any"})
    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    common_cells = adata.obs_names.intersection(counts_ad.obs_names)
    if len(common_cells) < 100:
        raise ValueError(
            f"too few cells shared between counts and processed: "
            f"{len(common_cells)} (<100)")
    sub = counts_ad[common_cells]
    npc = max(2, min(n_prin_comps, sub.n_vars - 1, sub.n_obs - 1))

    note = ""
    while True:  # 基因过滤后特征数可能 < npc（小数据），PCA 失败则减半重试
        scrub = scr.Scrublet(sub.X, expected_doublet_rate=expected_rate)
        try:
            score, pred = scrub.scrub_doublets(
                min_counts=2, min_cells=3, min_gene_variability_pctl=85,
                n_prin_comps=npc)
            break
        except ValueError as e:
            if "n_components" in str(e) and npc > 2:
                npc = max(2, npc // 2)
                note = f"n_prin_comps reduced to {npc} (few genes after filter)"
                continue
            raise
    if pred is None:  # 自动阈值失败（双峰不明显）→ 分位数回退
        thr = float(np.quantile(score, 1.0 - expected_rate))
        pred = score > thr
        note = (f"auto threshold failed; quantile fallback at "
                f"1-expected_rate ({thr:.3f})")

    score_s = pd.Series(np.asarray(score, dtype=float), index=common_cells)
    pred_s = pd.Series(np.asarray(pred, dtype=bool), index=common_cells)
    adata.obs["doublet_score"] = score_s.reindex(adata.obs_names)
    adata.obs["predicted_doublet"] = pred_s.reindex(adata.obs_names)

    out_dir = WS_ROOT / args["dataset_id"] / "doublet"
    out_dir.mkdir(parents=True, exist_ok=True)
    if celltype_col in adata.obs:
        rate = (adata.obs.groupby(celltype_col, observed=True)
                ["predicted_doublet"].agg(["mean", "count"])
                .rename(columns={"mean": "doublet_rate",
                                 "count": "n_cells"})
                .sort_values("doublet_rate", ascending=False))
        csv_path = out_dir / "doublet_rate_by_cluster.csv"
        rate.to_csv(csv_path)
    else:
        csv_path = ""

    fig, ax = plt.subplots(figsize=(6.5, 5))
    umap = adata.obsm["X_umap"]
    sc_plt = ax.scatter(umap[:, 0], umap[:, 1], s=5,
                        c=adata.obs["doublet_score"].to_numpy(
                            dtype=float, na_value=np.nan),
                        cmap="viridis", linewidths=0)
    mask = adata.obs["predicted_doublet"].fillna(False).to_numpy(dtype=bool)
    if mask.any():
        ax.scatter(umap[mask, 0], umap[mask, 1], s=14, facecolors="none",
                   edgecolors="red", linewidths=0.6)
    fig.colorbar(sc_plt, ax=ax, shrink=0.8, label="doublet score")
    ax.set_title(f"scrublet doublets (expected_rate={expected_rate}, "
                 f"predicted={int(mask.sum())})", fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    png_path = out_dir / "doublet_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_cells_scored": int(len(common_cells)),
        "n_doublets": int(mask.sum()),
        "doublet_rate": round(float(mask.sum() / adata.n_obs), 4),
        "doublet_score_quantiles": {
            "p50": round(float(score_s.quantile(0.5)), 3),
            "p90": round(float(score_s.quantile(0.9)), 3),
            "p99": round(float(score_s.quantile(0.99)), 3)},
        "csv": str(csv_path),
        "umap_png": str(png_path),
        "saved": str(h5ad_path),
        "note": note or "只标记不删除；过滤请用 sc_qc 语义另行决定",
    })


if __name__ == "__main__":
    run(main)
