"""st_integrate：多切片空间数据整合（Phase 69，L3 工具）。

stdin 契约（仅 dataset_refs 必填）：
  {"dataset_refs": ["sliceA", "sliceB"], "method": "harmony",
   "n_top_hvg": 2000, "n_pcs": 30, "n_neighbors": 15,
   "resolution": 1.0, "slice_col": "slice", "spatial_offset": false}

数据流（spec 2026-09-19-st-integrate-design.md §3）：逐片走
filtered.h5ad→raw.h5ad 回退链（counts 形态）→ anndata.concat
（slice 批次列 + 基因 outer join 补 0 + index_unique 防撞）→
全局 normalize→log1p→HVG→scale→PCA → harmony（默认）/bbknn 去批次
→ UMAP + leiden **表达域**聚类。★ 不做空间邻域图——跨切片坐标无
意义（与 st_process 的核心差异），下游空间分析按切片回各原始数据集。

产物（落 /ws/{new_id}/，new_id={ref1}__{ref2}[_etc]__{method}）：
  processed.h5ad / umap_integrated.png /
  spatial_slices.png（全片含 obsm["spatial"] 时）

spatial_offset=true：各片 x 坐标依次平移并排（gap=前片跨度 10%），
仅展示用——offset 后跨切片 spot 在空间 kNN 工具（st_stats/
st_nichenet 等）中会被误判邻居，勿将 merged 产物喂给此类工具。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from common import WS_ROOT, emit, fail, load_adata, read_args, run


def _die(code: str, msg: str) -> None:
    """结构化失败：common.fail 输出 JSON 后退出（错误码见 spec §8）。"""
    fail(code, msg)
    raise SystemExit(1)


def _new_id(refs: list[str], method: str) -> str:
    """产物目录名：{ref1}__{ref2}[_etc]__{method}——>2 片缩写 _etc，
    \\W 清洗（ref 内非法字符折为 _，双下划线分隔符保留）+ 80 长度帽。"""
    shown = refs if len(refs) <= 2 else [*refs[:2], "etc"]
    return re.sub(r"\W+", "_", "__".join(shown) + f"__{method}")[:80]


def _load_slice(ref: str) -> Any:
    """单片读入（load_adata 的 filtered→raw 回退链，counts 形态）；
    双双缺失时 ST_INTEG_REF_MISSING（提示先 st_load/st_qc）。"""
    ds_dir = WS_ROOT / ref
    if not ((ds_dir / "filtered.h5ad").exists()
            or (ds_dir / "raw.h5ad").exists()):
        _die("ST_INTEG_REF_MISSING",
             f"dataset {ref!r} has neither filtered.h5ad nor raw.h5ad "
             "under workspace — run st_load (ideally st_qc) first")
    return load_adata({"dataset_id": ref, "file": "any"})


def _offset_spatial(merged: Any, adatas: list[Any]) -> None:
    """各片 x 坐标依次平移并排：片 i 起点 = 前片终点 + 前片跨度 10%
    （in-place 改写 merged.obsm["spatial"]；行序 == adatas 拼接序）。"""
    cursor = 0.0
    shifted = []
    for a in adatas:
        sp = np.asarray(a.obsm["spatial"], dtype=float).copy()
        x0 = float(sp[:, 0].min())
        x1 = float(sp[:, 0].max())
        sp[:, 0] += cursor - x0
        shifted.append(sp)
        cursor += (x1 - x0) * 1.1
    merged.obsm["spatial"] = np.vstack(shifted)


def main() -> None:
    """主流程：merge → 全局预处理 → harmony/bbknn → UMAP/Leiden 表达域。"""
    import anndata as ad
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    refs_in = args.get("dataset_refs")
    if (not isinstance(refs_in, list) or len(refs_in) < 2
            or not all(isinstance(r, str) and r.strip() for r in refs_in)
            or len({r.strip() for r in refs_in}) != len(refs_in)):
        _die("INVALID_INPUT",
             "dataset_refs must be a list of >=2 distinct dataset ids")
    refs = [str(r).strip() for r in refs_in]
    method = str(args.get("method", "harmony")).strip().lower()
    if method not in ("harmony", "bbknn"):
        _die("INVALID_INPUT",
             f"unknown method {method!r}; expected 'harmony' or 'bbknn'")
    n_top_hvg = int(args.get("n_top_hvg", 2000))
    n_pcs = int(args.get("n_pcs", 30))
    n_neighbors = int(args.get("n_neighbors", 15))
    resolution = float(args.get("resolution", 1.0))
    slice_col = str(args.get("slice_col", "slice")).strip()
    spatial_offset = bool(args.get("spatial_offset", False))

    adatas = [_load_slice(r) for r in refs]
    for r, a in zip(refs, adatas):
        if slice_col in a.obs.columns:
            _die("INVALID_INPUT",
                 f"slice_col {slice_col!r} already exists in obs of "
                 f"{r!r}; pick another name")
    # spatial 仅在全片齐备且为 ≥2 列坐标时保留/可偏移（部分缺失时
    # concat 会静默丢 key——提前收敛为统一的"无 spatial"语义）
    has_spatial = all(
        "spatial" in a.obsm
        and np.asarray(a.obsm["spatial"]).ndim == 2
        and np.asarray(a.obsm["spatial"]).shape[1] >= 2
        for a in adatas)

    # 零交集在 concat 前直查（语义本身）：outer join 默认 fill NaN，
    # NaN 会传染 normalize/HVG 分箱，抢在拒收前炸 SCRIPT_ERROR
    common = set(adatas[0].var_names)
    for a in adatas[1:]:
        common &= set(a.var_names)
    if not common:
        _die("ST_INTEG_NO_OVERLAP",
             "gene panels of the slices share no genes — check gene "
             "naming consistency (hgnc vs ensembl?) across slices")

    # fill_value=0：缺失基因=未检测（部分交集场景同样需要，防 NaN 传染）
    merged = ad.concat(adatas, label=slice_col, keys=refs, join="outer",
                       index_unique="-", fill_value=0)
    slices = merged.obs[slice_col].astype(str)
    n_batch = int(slices.nunique())
    if n_batch < 2:
        _die("ST_INTEG_SINGLE_BATCH",
             f"merged {slice_col!r} has {n_batch} value(s); "
             "nothing to integrate")

    # 全局统一预处理：各片独立 HVG 有交集偏差，merge 后统一做是
    # harmony 教程标准口径（counts 形态必须，scaled 不可重 normalize）
    sc.pp.normalize_total(merged, target_sum=1e4)
    sc.pp.log1p(merged)
    sc.pp.highly_variable_genes(
        merged, n_top_genes=min(n_top_hvg, merged.n_vars),
        flavor="seurat")
    merged.raw = merged
    merged = merged[:, merged.var["highly_variable"]].copy()
    sc.pp.scale(merged, max_value=10)
    n_comps = min(n_pcs, merged.n_vars - 1, merged.n_obs - 1)
    sc.tl.pca(merged, n_comps=n_comps, svd_solver="arpack")

    # 双引擎分支（同 sc_integrate 口径）：产出收敛到 neighbors 图，
    # 下游 UMAP/Leiden 共用；engine_out 承载各引擎 emit 差异键
    engine_out: dict[str, Any] = {}
    if method == "bbknn":
        try:
            import bbknn
        except ImportError as e:
            raise RuntimeError(
                "bbknn not installed in image; rebuild bio image with "
                "bbknn pip layer") from e
        nwb = max(3, round(n_neighbors / n_batch))
        bbknn.bbknn(merged, batch_key=slice_col, neighbors_within_batch=nwb)
        engine_out["neighbors_within_batch"] = nwb
    else:
        try:
            import harmonypy
        except ImportError as e:
            raise RuntimeError(
                "harmonypy not installed in image; rebuild bio image with "
                "harmonypy pip layer") from e
        # 直调 harmonypy（scanpy 1.12 的 harmony_integrate 包装未适配
        # 2.x 的不转置约定，实测写 obsm 形状错）；Z_corr 按行数对齐
        # n_obs——2.x 为 (cells, pcs)，0.4.x 为 (pcs, cells) 需转置
        ho = harmonypy.run_harmony(
            np.asarray(merged.obsm["X_pca"], dtype=np.float64),
            merged.obs, slice_col, verbose=False)
        z = np.asarray(ho.Z_corr, dtype=np.float64)
        if z.shape[0] != merged.n_obs:
            z = z.T
        merged.obsm["X_pca_harmony"] = z
        sc.pp.neighbors(merged, n_neighbors=n_neighbors,
                        use_rep="X_pca_harmony")
        engine_out["representation"] = "X_pca_harmony"
    sc.tl.umap(merged)
    sc.tl.leiden(merged, resolution=resolution, flavor="igraph",
                 n_iterations=2, directed=False)

    if has_spatial and spatial_offset:
        _offset_spatial(merged, adatas)

    new_id = _new_id(refs, method)
    ds_dir = WS_ROOT / new_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    # UMAP 双联图：左按切片着色（混合程度）、右按 leiden 表达域
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), dpi=150)
    sc.pl.umap(merged, color=slice_col, ax=axes[0], show=False,
               title=f"Integrated UMAP ({slice_col})")
    sc.pl.umap(merged, color="leiden", ax=axes[1], show=False,
               legend_loc="on data",
               title=f"Integrated UMAP (leiden, res={resolution})")
    umap_png = ds_dir / "umap_integrated.png"
    fig.savefig(umap_png, bbox_inches="tight")
    plt.close(fig)
    merged.write_h5ad(ds_dir / "processed.h5ad")

    spatial_png: Path | None = None
    if has_spatial:
        fig, ax = plt.subplots(figsize=(6, 6), dpi=150)
        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        for i, key in enumerate(refs):
            m = (slices == key).to_numpy()
            sp = np.asarray(merged.obsm["spatial"])[m]
            ax.scatter(sp[:, 0], sp[:, 1], s=4,
                       color=colors[i % len(colors)], label=key)
        ax.set_title(f"Slices in space (offset={spatial_offset})")
        ax.legend(markerscale=3)
        ax.set_aspect("equal")
        spatial_png = ds_dir / "spatial_slices.png"
        fig.savefig(spatial_png, bbox_inches="tight")
        plt.close(fig)

    sizes = merged.obs["leiden"].value_counts().to_dict()
    note = ("leiden = expression domains (no spatial graph: cross-slice "
            "coordinates are meaningless) — run spatial analyses per "
            "original slice")
    if spatial_offset:
        note += ("; spatial coords are display-only offsets — do NOT feed "
                 "this dataset into spatial-kNN tools (st_stats/"
                 "st_nichenet etc.)")
    out: dict[str, Any] = {
        "ok": True,
        "dataset_ref": new_id,
        "parent_refs": refs,
        "method": method,
        "slice_col": slice_col,
        "n_slices": len(refs),
        "slice_sizes": {k: int(v) for k, v in slices.value_counts().items()},
        "n_cells": int(merged.n_obs),
        "n_genes": int(merged.n_vars),
        "n_clusters": int(merged.obs["leiden"].nunique()),
        "cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        "umap_png": str(umap_png),
        "spatial_offset": spatial_offset,
        "note": note,
        **engine_out,
    }
    if spatial_png is not None:
        out["spatial_png"] = str(spatial_png)
    emit(out)


if __name__ == "__main__":
    run(main)
