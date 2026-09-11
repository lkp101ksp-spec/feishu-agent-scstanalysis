"""st_niche：空间生态位重构（Phase 47）——细胞型组成 ward 层次聚类。

stdin: {"dataset_id": ..., "k": 12}
读 deconv.h5ad obsm["q05_cell_abundance_w_sf"]（cell2location 后验丰度，
列名去 q05cell_abundance_w_sf_ 前缀）行归一化为组成比例 →
scipy ward linkage + fcluster(maxclust=k) → niche 标签 N1..Nk（按尺寸
降序）写回 processed.h5ad obs["niche"]（st_plot 可着色、st_stats 可作
cluster_key）。产物落 /ws/{ds}/niche/：niche_spatial.png（分类着色
spatial_scatter）+ niche_composition_heatmap.png + niche_composition.csv。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import (
    WS_ROOT,
    emit,
    ensure_spatial,
    fail,
    load_adata,
    read_args,
    run,
)

ABUND_KEY = "q05_cell_abundance_w_sf"
ABUND_PREFIX = "q05cell_abundance_w_sf_"


def _load_composition(dataset_id: str,
                      obs_names: pd.Index) -> pd.DataFrame:
    """读 deconv.h5ad 组成矩阵并行归一化，与 processed obs_names 交集对齐。

    绝对丰度受 spot 细胞量干扰，niche 是组成概念 → 行归一化（tool-rctd
    权重行归一化口径）；零丰度行除零防御置 1。
    """
    import anndata as ad
    p = WS_ROOT / dataset_id / "deconv.h5ad"
    if not p.exists():
        fail("ST_NICHE_NO_DECONV",
             "deconv.h5ad 不存在；先跑 st_deconvolve")
        raise SystemExit(1)
    dec: Any = ad.read_h5ad(p)  # stub 返回 Any | Dataset2D，标 Any 收窄
    if ABUND_KEY not in dec.obsm:
        fail("INVALID_INPUT",
             f"deconv.h5ad 缺 obsm[{ABUND_KEY!r}]（非 st_deconvolve 产物？）")
        raise SystemExit(1)
    abund = dec.obsm[ABUND_KEY]
    if not isinstance(abund, pd.DataFrame):
        abund = pd.DataFrame(np.asarray(abund), index=dec.obs_names)
    abund = abund.copy()
    abund.columns = [str(c).replace(ABUND_PREFIX, "")
                     for c in abund.columns]
    common = obs_names.intersection(abund.index)
    if len(common) < 100:
        fail("INVALID_INPUT",
             f"deconv 与 processed 共有 spot 过少: {len(common)} (<100)")
        raise SystemExit(1)
    comp = abund.loc[common].astype(float)
    row_sum = comp.sum(axis=1)
    row_sum = row_sum.where(row_sum > 0, 1.0)
    return comp.div(row_sum, axis=0)


def _cluster_niches(comp: pd.DataFrame, k: int) -> pd.Series:
    """ward 层次聚类 maxclust=k → niche 标签 Series（N1..Nk 按尺寸降序）。"""
    from scipy.cluster.hierarchy import fcluster, linkage
    z = linkage(comp.to_numpy(dtype=np.float64), method="ward")
    raw = fcluster(z, t=k, criterion="maxclust")
    sizes = pd.Series(raw).value_counts()  # value_counts 默认降序
    remap = {c: f"N{i + 1}" for i, c in enumerate(sizes.index)}
    return pd.Series([remap[c] for c in raw], index=comp.index,
                     name="niche")


def _composition_heatmap(comp: pd.DataFrame, niche: pd.Series,
                         png_path: Path) -> pd.DataFrame:
    """niche × 细胞型均值组成热图（行=niche 按 dominant 型分组排序，
    行标签附 dominant 型）。返回矩阵（供 csv 与 emit dominant）。"""
    import matplotlib.pyplot as plt
    mat = comp.groupby(niche).mean()
    dom = mat.idxmax(axis=1)
    order = sorted(mat.index, key=lambda n: (str(dom[n]), str(n)))
    mat = mat.loc[order]
    fig, ax = plt.subplots(
        figsize=(max(6.0, mat.shape[1] * 0.5),
                 max(4.0, mat.shape[0] * 0.35 + 1.5)))
    im = ax.imshow(mat.to_numpy(), aspect="auto", cmap="viridis")
    ax.set_xticks(range(mat.shape[1]))
    ax.set_xticklabels([str(c) for c in mat.columns], rotation=45,
                       ha="right", fontsize=7)
    ax.set_yticks(range(mat.shape[0]))
    ax.set_yticklabels([f"{n} ({dom[n]})" for n in mat.index], fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.7, label="mean composition")
    ax.set_title("Niche composition (row = niche, label = dominant type)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return mat


def _scatter_img_kwargs(adata: Any) -> dict[str, Any]:
    """spatial_scatter 图像参数（st_stats/st_cnv 同款）：无 uns['spatial']
    补占位壳 + img=False；有真实组织图走默认带图。"""
    sp = adata.uns.get("spatial")
    if not isinstance(sp, dict) or not sp:
        adata.uns["spatial"] = {"_placeholder": {
            "images": {"hires": np.zeros((8, 8, 3))},
            "scalefactors": {"tissue_hires_scalef": 1.0,
                             "spot_diameter_fullres": 1.0}}}
        return {"img": False}
    has_img = any(isinstance(lib, dict) and lib.get("images")
                  for lib in sp.values())
    return {} if has_img else {"img": False}


def main() -> None:
    """主流程：组成矩阵 → ward 聚类 → 热图/csv → 写回+空间图 → emit。"""
    args = read_args()
    k = int(args.get("k", 12))
    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    comp = _load_composition(args["dataset_id"], adata.obs_names)
    if k < 2 or k >= len(comp):
        fail("INVALID_INPUT",
             f"k={k} 越界（需 2 ≤ k < 有效 spot 数 {len(comp)}）")
        raise SystemExit(1)
    niche = _cluster_niches(comp, k)

    out_dir = WS_ROOT / args["dataset_id"] / "niche"
    out_dir.mkdir(parents=True, exist_ok=True)
    heat_png = out_dir / "niche_composition_heatmap.png"
    mat = _composition_heatmap(comp, niche, heat_png)
    csv_path = out_dir / "niche_composition.csv"
    mat.to_csv(csv_path, index_label="niche")

    # 写回 processed.h5ad（reindex 对齐；st_plot/st_stats 可复用）
    adata.obs["niche"] = niche.reindex(adata.obs_names)
    import matplotlib.pyplot as plt
    import squidpy as sq
    img_kw = _scatter_img_kwargs(adata)
    sp_png = out_dir / "niche_spatial.png"
    ax = sq.pl.spatial_scatter(adata, color=["niche"],
                               return_ax=True, **img_kw)
    ax.set_title("Spatial niche")
    ax.figure.savefig(sp_png, dpi=150, bbox_inches="tight")
    plt.close("all")
    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)

    sizes = niche.value_counts()
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "k": k,
        "n_niches": int(niche.nunique()),
        "niche_sizes": {str(n): int(s) for n, s in sizes.items()},
        "dominant_by_niche": {str(n): str(mat.loc[n].idxmax())
                              for n in mat.index},
        "cell_types": [str(c) for c in comp.columns],
        "n_spots": int(len(niche)),
        # pngs 聚合键：IM 发图与 D 报告共用宿主四键收集
        "pngs": [str(sp_png), str(heat_png)],
        "niche_spatial_png": str(sp_png),
        "composition_heatmap_png": str(heat_png),
        "composition_csv": str(csv_path),
        "saved": str(h5ad_path),
        "note": "组成矩阵行归一化 + ward 层次聚类（maxclust）；"
                "obs['niche'] 已写回 processed.h5ad，st_plot 可作 "
                "color_by、st_stats 可作 cluster_key",
    })


if __name__ == "__main__":
    run(main)
