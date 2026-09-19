"""sc_genescore：PROGENy 14 通路活性打分（Phase 72，decoupler 2.x MLM）。

stdin: {"dataset_id": ..., "groupby": "leiden", "top_n": 14}
需 processed.h5ad（人类 symbol，raw 层优先）；模型为构建期快照
/opt/progeny/progeny_human_top500.tsv（Phase 50 fetch_progeny.py 产物，
与 st_misty progeny 视图同源），运行期断网可用。
dc.mt.mlm（加权线性回归，tmin=5）→ obsm['score_mlm']（细胞×14 通路）；
per-cell 全矩阵只落 csv，JSON/热图只带组×通路均值与组间方差排序（防膨胀）。
仅支持 human（PROGENy 无 mouse 模型）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run, species_style_guard

PROGENY_TSV = Path("/opt/progeny/progeny_human_top500.tsv")
MIN_GENE_OVERLAP = 100  # st_misty 同口径：靶基因总交集下限，防物种不符
MIN_PATHWAYS = 7  # 14 通路至少活下来一半，否则视为低重叠


def main() -> None:
    """主流程：PROGENy MLM 打分 → 组×通路均值 + 方差排序热图/UMAP。"""
    import decoupler as dc
    import matplotlib.pyplot as plt

    args = read_args()
    groupby = str(args.get("groupby", "leiden")).strip()
    top_n = int(args.get("top_n", 14))
    species = str(args.get("species", "human")).strip().lower()
    if species != "human":
        fail("INVALID_INPUT", f"sc_genescore 仅支持 human（PROGENy 无 mouse 模型）；got {species!r}")
        raise SystemExit(1)

    if not PROGENY_TSV.exists():
        fail("GENESCORE_NO_MODEL", f"{PROGENY_TSV} 缺失（镜像快照层异常，重建 bio 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(PROGENY_TSV, sep="\t")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    species_style_guard(species, adata.var_names, "SC_SPECIES_MISMATCH")
    has_raw = adata.raw is not None
    universe = set(adata.raw.var_names) if has_raw else set(adata.var_names)

    n_overlap = len(set(net["target"]) & universe)
    if n_overlap < MIN_GENE_OVERLAP:
        fail(
            "GENESCORE_LOW_OVERLAP",
            f"PROGENy 靶基因与数据交集过少: {n_overlap} (<{MIN_GENE_OVERLAP}，基因名需为人类 symbol)",
        )
        raise SystemExit(1)

    if groupby not in adata.obs.columns:
        fail("INVALID_INPUT", f"groupby 列 {groupby!r} 不在 obs；可用列: {list(adata.obs.columns)[:20]}")
        raise SystemExit(1)

    dc.mt.mlm(adata, net, tmin=5, raw=has_raw, verbose=False)
    scores = adata.obsm["score_mlm"].astype(np.float32)
    dead = [c for c in scores.columns if bool(scores[c].isna().all())]
    scores = scores.drop(columns=dead)
    if scores.shape[1] < MIN_PATHWAYS:
        fail(
            "GENESCORE_LOW_OVERLAP", f"可打分通路仅 {scores.shape[1]} (<{MIN_PATHWAYS}，全 NaN 通路: {dead})"
        )
        raise SystemExit(1)

    grp = adata.obs[groupby].astype(str)
    ds_dir = WS_ROOT / args["dataset_id"] / "genescore"
    ds_dir.mkdir(parents=True, exist_ok=True)

    full = scores.copy()
    full.insert(0, groupby, grp.values)
    scores_csv = ds_dir / "progeny_scores.csv"
    full.to_csv(scores_csv)

    group_mean = full.groupby(groupby, sort=False).mean()
    order = sorted(group_mean.index, key=lambda c: (len(c), c))
    group_mean = group_mean.loc[order]
    mean_csv = ds_dir / "progeny_group_mean.csv"
    group_mean.to_csv(mean_csv)

    variances = group_mean.var(axis=0)
    top = variances.sort_values(ascending=False).head(top_n)
    top_terms = list(top.index)
    hm = group_mean[top_terms].to_numpy(dtype=float)
    mu = hm.mean(axis=1, keepdims=True)
    sd = hm.std(axis=1, keepdims=True)
    z = np.divide(hm - mu, sd, out=np.zeros_like(hm), where=sd > 0)
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(top_terms) + 1.6))
    im = ax.imshow(z, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, fontsize=9)
    ax.set_yticks(range(len(top_terms)))
    ax.set_yticklabels(top_terms, fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z-score (per pathway)")
    ax.set_title(f"PROGENy pathway activity by {groupby}", fontsize=10)
    fig.tight_layout()
    heatmap_png = ds_dir / "progeny_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top_col = top_terms[0]
    umap_png = None
    if "X_umap" in adata.obsm:
        umap = np.asarray(adata.obsm["X_umap"])
        fig, ax = plt.subplots(figsize=(5, 4))
        s = ax.scatter(
            umap[:, 0], umap[:, 1], s=4, c=scores[top_col].to_numpy(dtype=float), cmap="viridis", linewidths=0
        )
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(f"PROGENy: {top_col}", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        umap_png = ds_dir / "progeny_umap.png"
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    emit(
        {
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "species": species,
            "groupby": groupby,
            "method": "progeny_mlm",
            "n_cells": int(adata.n_obs),
            "n_pathways_total": int(net["source"].nunique()),
            "n_pathways_scored": int(scores.shape[1]),
            "dropped_pathways": dead,
            "top_pathways": [
                {
                    "pathway": t,
                    "variance": round(float(top[t]), 6),
                    "group_means": {c: round(float(group_mean.loc[c, t]), 4) for c in order},
                }
                for t in top_terms
            ],
            "scores_csv": str(scores_csv),
            "group_mean_csv": str(mean_csv),
            "heatmap_png": str(heatmap_png),
            "umap_png": str(umap_png) if umap_png else None,
        }
    )


if __name__ == "__main__":
    run(main)
