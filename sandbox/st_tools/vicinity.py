"""st_vicinity：肿瘤邻域分层（Phase 48）——恶性种子沿空间邻居图 BFS。

stdin: {"dataset_id": ..., "max_layers": 5, "coord_type": "grid"}
种子=obs["is_malignant"]==True（st_cnv 写回；缺列/零恶性报
ST_VICINITY_NO_SEED）。邻居图复用 obsp["spatial_connectivities"]
（st_process 已建；缺失按 coord_type 补建，st_stats 同款）。BFS 用
scipy.sparse.csgraph.shortest_path(unweighted=True) 一次求全图距离场：
dist=0 → tumor，1..max_layers → L1..Ln，其余（含不可达 inf）→ distal。
obs["vicinity"] 写回 processed.h5ad。产物落 /ws/{ds}/vicinity/：
vicinity_spatial.png + vicinity_layer_sizes.csv；deconv.h5ad 存在时
追加 vicinity_composition.csv + vicinity_composition_heatmap.png
（层×细胞型均值组成，行按肿瘤 proximity 排序）。
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
COORD_TYPES = ("grid", "generic")


def _build_neighbors(adata: Any, coord_type: str, n_neighs: int = 6) -> None:
    """复用 st_process 已建邻居图；缺失才按 coord_type 补建（st_stats 同款）。"""
    if "spatial_connectivities" in adata.obsp:
        return
    import squidpy as sq
    if coord_type == "generic":
        sq.gr.spatial_neighbors(adata, coord_type="generic", delaunay=True)
    else:
        sq.gr.spatial_neighbors(adata, coord_type="grid", n_neighs=n_neighs)


def _layer_labels(dist: np.ndarray, seed: np.ndarray,
                  max_layers: int) -> np.ndarray:
    """距离场 → 层标签：0=tumor，1..max=L1..Ln，其余（含 inf）=distal。"""
    labels = np.full(len(dist), "distal", dtype=object)
    labels[seed] = "tumor"
    for layer in range(1, max_layers + 1):
        labels[(dist == layer) & ~seed] = f"L{layer}"
    return labels


def _vicinity_spatial_png(adata: Any, png_path: Path) -> None:
    """分层着色 spatial_scatter（占位壳模式；类目按 tumor→Ln→distal 排序）。"""
    import matplotlib.pyplot as plt
    import squidpy as sq
    sp = adata.uns.get("spatial")
    if not isinstance(sp, dict) or not sp:
        adata.uns["spatial"] = {"_placeholder": {
            "images": {"hires": np.zeros((8, 8, 3))},
            "scalefactors": {"tissue_hires_scalef": 1.0,
                             "spot_diameter_fullres": 1.0}}}
        img_kw: dict[str, Any] = {"img": False}
    else:
        has_img = any(isinstance(lib, dict) and lib.get("images")
                      for lib in sp.values())
        img_kw = {} if has_img else {"img": False}
    ax = sq.pl.spatial_scatter(adata, color=["vicinity"],
                               return_ax=True, **img_kw)
    ax.set_title("Tumor vicinity layers")
    ax.figure.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close("all")


def _composition_products(adata: Any, dataset_id: str, labels: pd.Series,
                          out_dir: Path) -> tuple[Path | None, Path | None]:
    """层×细胞型均值组成 csv+热图（deconv.h5ad 存在才做，缺失返回 (None, None)）。

    行按肿瘤 proximity 排序（tumor→L1..→distal）；组成矩阵行归一化
    （st_niche 同款加载逻辑）。分层本身不依赖反卷积，缺失不报错。
    """
    p = WS_ROOT / dataset_id / "deconv.h5ad"
    if not p.exists():
        return None, None
    import anndata as ad
    dec: Any = ad.read_h5ad(p)  # stub 返回 Any | Dataset2D，标 Any 收窄
    if ABUND_KEY not in dec.obsm:
        return None, None
    abund = dec.obsm[ABUND_KEY]
    if not isinstance(abund, pd.DataFrame):
        abund = pd.DataFrame(np.asarray(abund), index=dec.obs_names)
    abund = abund.copy()
    abund.columns = [str(c).replace(ABUND_PREFIX, "")
                     for c in abund.columns]
    common = labels.index.intersection(abund.index)
    if len(common) < 100:
        return None, None
    comp = abund.loc[common].astype(float)
    row_sum = comp.sum(axis=1)
    row_sum = row_sum.where(row_sum > 0, 1.0)
    comp = comp.div(row_sum, axis=0)
    mat = comp.groupby(labels.loc[common]).mean()
    order = ["tumor"] + sorted(
        [i for i in mat.index if str(i).startswith("L")],
        key=lambda s: int(str(s)[1:])) + ["distal"]
    mat = mat.loc[[i for i in order if i in mat.index]]
    csv_path = out_dir / "vicinity_composition.csv"
    mat.to_csv(csv_path, index_label="vicinity")
    import matplotlib.pyplot as plt
    png_path = out_dir / "vicinity_composition_heatmap.png"
    fig, ax = plt.subplots(
        figsize=(max(6.0, mat.shape[1] * 0.5),
                 max(3.5, mat.shape[0] * 0.4 + 1.5)))
    im = ax.imshow(mat.to_numpy(), aspect="auto", cmap="viridis")
    ax.set_xticks(range(mat.shape[1]))
    ax.set_xticklabels([str(c) for c in mat.columns], rotation=45,
                       ha="right", fontsize=7)
    ax.set_yticks(range(mat.shape[0]))
    ax.set_yticklabels([str(i) for i in mat.index], fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.7, label="mean composition")
    ax.set_title("Vicinity layer composition (tumor proximity order)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return csv_path, png_path


def main() -> None:
    """主流程：种子/邻居图 → BFS 距离场分层 → 写回+产物 → emit。"""
    args = read_args()
    max_layers = int(args.get("max_layers", 5))
    coord_type = str(args.get("coord_type", "grid"))
    if max_layers < 1 or max_layers > 10:
        fail("INVALID_INPUT",
             f"max_layers={max_layers} 越界（需 1..10）")
        raise SystemExit(1)
    if coord_type not in COORD_TYPES:
        fail("INVALID_INPUT",
             f"coord_type={coord_type!r} 非白名单 {COORD_TYPES}")
        raise SystemExit(1)

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    if "is_malignant" not in adata.obs:
        fail("ST_VICINITY_NO_SEED",
             "obs 缺 is_malignant 列；先跑 st_cnv")
        raise SystemExit(1)
    seed = adata.obs["is_malignant"].astype(bool).to_numpy()
    n_tumor = int(seed.sum())
    if n_tumor == 0:
        fail("ST_VICINITY_NO_SEED",
             "is_malignant 全 False（st_cnv 未检出恶性）；"
             "确认数据含肿瘤或检查 st_cnv 参考选择")
        raise SystemExit(1)

    _build_neighbors(adata, coord_type)
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import shortest_path
    graph = csr_matrix(adata.obsp["spatial_connectivities"])
    dist = shortest_path(graph, directed=False, unweighted=True,
                         indices=np.where(seed)[0]).min(axis=0)
    labels = _layer_labels(dist, seed, max_layers)
    vicinity = pd.Series(labels, index=adata.obs_names, name="vicinity")
    layer_order = ["tumor"] + [f"L{i}" for i in range(1, max_layers + 1)] \
        + ["distal"]
    adata.obs["vicinity"] = pd.Categorical(
        vicinity, categories=layer_order, ordered=True)

    out_dir = WS_ROOT / args["dataset_id"] / "vicinity"
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = vicinity.value_counts()
    sizes = sizes.reindex([i for i in layer_order if i in sizes.index])
    sizes_csv = out_dir / "vicinity_layer_sizes.csv"
    sizes.rename_axis("vicinity").to_csv(sizes_csv, header=["n_spots"])
    sp_png = out_dir / "vicinity_spatial.png"
    _vicinity_spatial_png(adata, sp_png)
    comp_csv, comp_png = _composition_products(
        adata, args["dataset_id"], vicinity, out_dir)

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)

    pngs = [str(sp_png)] + ([str(comp_png)] if comp_png else [])
    n_reached = int((vicinity != "distal").sum())
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "max_layers": max_layers,
        "n_tumor": n_tumor,
        "n_reached": n_reached,
        "layer_sizes": {str(k): int(v) for k, v in sizes.items()},
        "has_composition": comp_png is not None,
        # pngs 聚合键：IM 发图与 D 报告共用宿主四键收集
        "pngs": pngs,
        "vicinity_spatial_png": str(sp_png),
        "layer_sizes_csv": str(sizes_csv),
        "composition_csv": str(comp_csv) if comp_csv else None,
        "composition_heatmap_png": str(comp_png) if comp_png else None,
        "saved": str(h5ad_path),
        "note": "BFS 距离场分层（unweighted shortest_path）；"
                "obs['vicinity'] 已写回 processed.h5ad"
                + ("" if comp_png else "；deconv.h5ad 缺失，层×细胞型"
                                        "组成统计跳过"),
    })


if __name__ == "__main__":
    run(main)
