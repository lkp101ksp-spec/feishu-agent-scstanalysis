"""st_genescore：spot 级 PROGENy 14 通路活性打分（Phase 75 空间版）。

stdin: {"dataset_id": ..., "groupby": "spatial_domain", "top_n": 14}
与 sc 版（genescore.py）三点差异（phase75 spec §3.1）：
①载入后校验 obsm["spatial"] 形状 (n,≥2)，无→INVALID_INPUT 引导
  sc_genescore；
②主图换空间着色图（方差 top1 通路 × obsm.spatial，等比坐标），UMAP
  存在则附加产出；
③groupby 默认 spatial_domain（sc 版 leiden），域数 <2 拒收引导换列。
模型快照/MLM 算法/低重叠门槛与 sc 版逐行一致；产物目录 {ds}/
st_genescore/ 与 sc 版 genescore/ 互不覆盖。仅支持 human（PROGENy
无 mouse 模型）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run, species_style_guard

PROGENY_TSV = Path("/opt/progeny/progeny_human_top500.tsv")
MIN_GENE_OVERLAP = 100  # 与 sc 版/st_misty 同口径：靶基因总交集下限
MIN_PATHWAYS = 7  # 14 通路至少活下来一半，否则视为低重叠
MIN_DOMAINS = 2  # 单域无组间方差可言，引导换列（spec §5 风险对策）


def main() -> None:
    """主流程：PROGENy MLM spot 级打分 → 域×通路均值 + 空间着色图。"""
    import decoupler as dc
    import matplotlib.pyplot as plt

    args = read_args()
    groupby = str(args.get("groupby", "spatial_domain")).strip()
    top_n = int(args.get("top_n", 14))

    if not PROGENY_TSV.exists():
        fail("GENESCORE_NO_MODEL", f"{PROGENY_TSV} 缺失（镜像快照层异常，重建 bio 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(PROGENY_TSV, sep="\t")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    sp = np.asarray(adata.obsm.get("spatial", []))
    if sp.ndim != 2 or sp.shape[1] < 2 or sp.shape[0] != adata.n_obs:
        fail("INVALID_INPUT",
             "缺 obsm['spatial']（需 (n,≥2) 空间坐标）；本工具为 spot 级"
             "空间版，普通 sc 数据请用 sc_genescore")
        raise SystemExit(1)
    species_style_guard("human", adata.var_names, "SC_SPECIES_MISMATCH")
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
    grp = adata.obs[groupby].astype(str)
    if grp.nunique() < MIN_DOMAINS:
        fail("INVALID_INPUT",
             f"groupby {groupby!r} 仅 {grp.nunique()} 个域（<{MIN_DOMAINS} 无组间方差）；"
             "spatial 数据建议 spatial_domain，或显式传其它注释列")
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

    ds_dir = WS_ROOT / args["dataset_id"] / "st_genescore"
    ds_dir.mkdir(parents=True, exist_ok=True)

    full = scores.copy()
    full.insert(0, groupby, grp.values)
    scores_csv = ds_dir / "st_progeny_scores.csv"
    full.to_csv(scores_csv)

    group_mean = full.groupby(groupby, sort=False).mean()
    order = sorted(group_mean.index, key=lambda c: (len(c), c))
    group_mean = group_mean.loc[order]
    mean_csv = ds_dir / "st_progeny_group_mean.csv"
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
    ax.set_title(f"PROGENy pathway activity by {groupby} (spatial)", fontsize=10)
    fig.tight_layout()
    heatmap_png = ds_dir / "st_progeny_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 空间着色主图（st 版差异②）：方差 top1 通路 × obsm.spatial，等比防组织形变
    top_col = top_terms[0]
    fig, ax = plt.subplots(figsize=(5, 4))
    s = ax.scatter(
        sp[:, 0], sp[:, 1], s=7,
        c=scores[top_col].to_numpy(dtype=float), cmap="viridis", linewidths=0
    )
    fig.colorbar(s, ax=ax, fraction=0.046)
    ax.set_title(f"PROGENy spatial: {top_col}", fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    spatial_png = ds_dir / "st_progeny_spatial.png"
    fig.savefig(spatial_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    umap_png = None
    if "X_umap" in adata.obsm:
        umap = np.asarray(adata.obsm["X_umap"])
        fig, ax = plt.subplots(figsize=(5, 4))
        s = ax.scatter(
            umap[:, 0], umap[:, 1], s=4,
            c=scores[top_col].to_numpy(dtype=float), cmap="viridis", linewidths=0
        )
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(f"PROGENy: {top_col}", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        umap_png = ds_dir / "st_progeny_umap.png"
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    top_by_group = {
        c: group_mean.loc[c].sort_values(ascending=False).head(3).index.tolist()
        for c in order
    }
    emit(
        {
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "groupby": groupby,
            "method": "progeny_mlm",
            "n_spots": int(adata.n_obs),
            "n_pathways_total": int(net["source"].nunique()),
            "n_pathways_scored": int(scores.shape[1]),
            "n_domains": int(grp.nunique()),
            "dropped_pathways": dead,
            "top_by_group": top_by_group,
            "products": {
                "scores_csv": str(scores_csv),
                "group_mean_csv": str(mean_csv),
                "heatmap_png": str(heatmap_png),
                "spatial_png": str(spatial_png),
                "umap_png": str(umap_png) if umap_png else None,
            },
        }
    )


if __name__ == "__main__":
    run(main)
