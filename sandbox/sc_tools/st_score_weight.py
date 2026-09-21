"""st_score_weight：反卷积加权打分（Phase 76，spec 2026-09-21 §2-§4）。

stdin: {"dataset_id": ..., "source": "st_genescore"|"st_metabolism"}
读两路产物做"细胞型 × 通路"丰度加权活性矩阵：
- {ds}/deconv.h5ad → obsm["q05_cell_abundance_w_sf"]（spot × 细胞型，
  deconvolve.py 已把列名净化为纯因子名，本脚本再做防御性前缀剥离）；
- {ds}/{source}/{scores csv}（spot × 通路，首列为 groupby 丢弃）。
口径 W = (Aᵀ @ S) / A.colsum()（丰度加权均值，与 spot 分同尺度、
细胞型间可比；裸矩阵乘会把"细胞多"与"活性高"混淆）；总丰度 <1e-6
的细胞型剔除并上报 dropped_celltypes；spot 索引重合率 <80% 硬拒收。
产物落 {ds}/st_score_weight/（csv 全量 + 列 z-score 热图方差 top30）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, read_args, run

SCORES_MAP = {
    "st_genescore": ("st_genescore", "st_progeny_scores.csv"),
    "st_metabolism": ("st_metabolism", "st_metabolism_scores.csv"),
}
MIN_OVERLAP_RATIO = 0.8  # spot 索引重合率下限，防错数据集张冠李戴
MIN_ABUND = 1e-6  # 细胞型总丰度下限，防 0/0 出 NaN 静默入产物
HEATMAP_TOP_N = 30  # 代谢 315 通路全画不可读，热图只取方差 top30 列


def main() -> None:
    """主流程：q05 丰度 × spot 通路分 → 细胞型×通路加权矩阵 + 热图。"""
    import anndata as ad
    import matplotlib.pyplot as plt

    args = read_args()
    source = str(args.get("source", "st_genescore")).strip()
    if source not in SCORES_MAP:
        fail("INVALID_INPUT",
             f"source 需为 {sorted(SCORES_MAP)} 之一，收到 {source!r}")
        raise SystemExit(1)
    sub_dir, scores_name = SCORES_MAP[source]

    ds_dir_in = WS_ROOT / args["dataset_id"]
    deconv_h5ad = ds_dir_in / "deconv.h5ad"
    if not deconv_h5ad.exists():
        fail("ST_WEIGHT_NO_DECONV",
             f"{deconv_h5ad} 缺失：请先运行 st_deconvolve 生成反卷积产物")
        raise SystemExit(1)
    scores_csv = ds_dir_in / sub_dir / scores_name
    if not scores_csv.exists():
        fail("INVALID_INPUT",
             f"{scores_csv} 缺失：请先运行 {source} 生成 spot 级打分产物")
        raise SystemExit(1)

    sp = ad.read_h5ad(deconv_h5ad)
    abund = sp.obsm.get("q05_cell_abundance_w_sf")
    if abund is None or abund.shape[1] == 0:
        fail("ST_WEIGHT_NO_DECONV",
             "deconv.h5ad 缺 obsm['q05_cell_abundance_w_sf']（结构异常，"
             "请重跑 st_deconvolve）")
        raise SystemExit(1)
    abund = abund.copy()
    abund.columns = [str(c).replace("q05cell_abundance_w_sf_", "")
                     for c in abund.columns]
    abund.index = abund.index.astype(str)

    sc_df = pd.read_csv(scores_csv, index_col=0)
    sc_df.index = sc_df.index.astype(str)
    scores = sc_df.iloc[:, 1:].astype(float)  # 首列 groupby 丢弃

    common = abund.index.intersection(scores.index)
    ratio = len(common) / max(len(scores.index), 1)
    if ratio < MIN_OVERLAP_RATIO:
        fail("INVALID_INPUT",
             f"spot 索引重合率 {ratio:.1%}（{len(common)}/"
             f"{len(scores.index)}）<{MIN_OVERLAP_RATIO:.0%}：scores 与 "
             "deconv 疑似不同数据集产物")
        raise SystemExit(1)
    a_mat = abund.loc[common].to_numpy(dtype=float)
    s_mat = scores.loc[common].to_numpy(dtype=float)

    colsum = a_mat.sum(axis=0)
    keep = colsum >= MIN_ABUND
    dropped = [str(c) for c, k in zip(abund.columns, keep) if not k]
    if not keep.any():
        fail("INVALID_INPUT",
             "全部细胞型总丰度近零（deconv 产物异常，请重跑 st_deconvolve）")
        raise SystemExit(1)
    cell_types = [str(c) for c, k in zip(abund.columns, keep) if k]
    w = (a_mat[:, keep].T @ s_mat) / colsum[keep][:, None]
    w_df = pd.DataFrame(w, index=cell_types, columns=scores.columns)

    out_dir = WS_ROOT / args["dataset_id"] / "st_score_weight"
    out_dir.mkdir(parents=True, exist_ok=True)
    w_csv = out_dir / f"st_weighted_{source}_scores.csv"
    w_df.to_csv(w_csv)

    top_cols = (w_df.var(axis=0).sort_values(ascending=False)
                .head(HEATMAP_TOP_N).index.tolist())
    hm = w_df[top_cols].to_numpy(dtype=float)
    mu = hm.mean(axis=0, keepdims=True)
    sd = hm.std(axis=0, keepdims=True)
    z = np.divide(hm - mu, sd, out=np.zeros_like(hm), where=sd > 0)
    fig, ax = plt.subplots(
        figsize=(max(6.0, 0.32 * len(top_cols)),
                 0.3 * len(cell_types) + 1.6))
    im = ax.imshow(z, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(len(top_cols)))
    ax.set_xticklabels([c[:18] for c in top_cols], rotation=60,
                       ha="right", fontsize=7)
    ax.set_yticks(range(len(cell_types)))
    ax.set_yticklabels(cell_types, fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z-score (per pathway)")
    ax.set_title(f"Deconv-weighted activity ({source})", fontsize=10)
    fig.tight_layout()
    heatmap_png = out_dir / f"st_weighted_{source}_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top_by_celltype = {
        ct: w_df.loc[ct].sort_values(ascending=False).head(3).index.tolist()
        for ct in cell_types
    }
    emit(
        {
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "source": source,
            "n_celltypes": len(cell_types),
            "n_pathways": int(w_df.shape[1]),
            "n_spots_overlap": int(len(common)),
            "dropped_celltypes": dropped,
            "top_by_celltype": top_by_celltype,
            "products": {
                "scores_csv": str(w_csv),
                "heatmap_png": str(heatmap_png),
            },
        }
    )


if __name__ == "__main__":
    run(main)
