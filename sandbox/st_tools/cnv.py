"""st_cnv：空间 spot 级 CNV 推断与恶性判定（Phase 46，infercnvpy 单后端）。

stdin: {"dataset_id": ..., "annotation_key": "",   # obs 注释列；"" 走回退链
        "ref_groups": ["T cells", ...],            # 显式参考；缺省内置清单
        "resolution": 1.0}                         # 亚克隆 leiden 分辨率
数据流（sc_cnv.py 同构）：counts 走 filtered/raw 回退链，标签/坐标走
processed.h5ad 按 obs_names 交集对齐。annotation_key="deconv" 特殊值：
读 deconv.h5ad 取 RCTD 权重最大型作标签（生物名可命中内置非恶性清单）。
产物落 /ws/{ds}/cnv/：figS3B 式染色体热图 + cnv_score/cnv_subclone
spatial_scatter 组织图（sc 版 UMAP 图的空间替代）+ 注释×恶性 csv +
亚克隆×染色体 csv。cnv_score/is_malignant/cnv_subclone 写回
processed.h5ad（st_plot 可直接着色）。
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

GENE_POS_TSV = "/opt/cnv/gene_pos_grch38.tsv"
CHROM_ORDER = [f"chr{i}" for i in range(1, 23)] + ["chrX", "chrY"]
# 人源常见非恶性参考（大小写不敏感子串匹配，与 sc_cnv 同清单）。
# Epithelial 故意不在默认清单（可为恶性来源），靠 ref_groups 显式加。
DEFAULT_REF_PATTERNS = [
    "T cell", "B cell", "NK", "Macrophage", "Monocyte", "Dendritic",
    "Neutrophil", "Fibroblast", "Endothelial", "Pericyte",
    "Smooth muscle", "Erythrocyte",
]
ANNOTATION_FALLBACK = ["cell_type", "spatial_domain", "leiden"]


class NoReferenceError(ValueError):
    """默认清单与注释取值零匹配（不静默降级为无参考模式）。"""


def _match_references(values: list[str],
                      ref_groups: list[str] | None) -> list[str]:
    """解析参考类型：显式 ref_groups 直接用；缺省内置清单子串匹配。"""
    if ref_groups:
        missing = [g for g in ref_groups if g not in values]
        if missing:
            raise ValueError(
                f"ref_groups not in column values: {missing}; "
                f"available: {values}")
        return list(ref_groups)
    matched = [v for v in values if any(
        p.lower() in v.lower() for p in DEFAULT_REF_PATTERNS)]
    if not matched:
        raise NoReferenceError(
            "no annotation value matches the default non-malignant list "
            "(T/B/NK/Macrophage/Monocyte/Dendritic/Neutrophil/Fibroblast/"
            "Endothelial/Pericyte/Smooth muscle/Erythrocyte); pass "
            'ref_groups explicitly or annotation_key="deconv", e.g. '
            'ref_groups=["T cells", "Fibroblast"]; '
            f"available values: {values}")
    return matched


def _resolve_labels(adata: Any, dataset_id: str,
                    annotation_key: str) -> tuple[pd.Series, str]:
    """解析 spot 标签（index=adata.obs_names 的 str Series）+ 来源描述。

    deconv 特殊值：读 deconv.h5ad（st_deconvolve 产物，obs 列=细胞类型
    权重）逐 spot 取 argmax；缺失报 ST_CNV_NO_DECONV。显式列校验存在；
    空走回退链 cell_type→spatial_domain→leiden，全灭 INVALID_INPUT。
    """
    if annotation_key == "deconv":
        p = WS_ROOT / dataset_id / "deconv.h5ad"
        if not p.exists():
            fail("ST_CNV_NO_DECONV",
                 "annotation_key='deconv' 但 deconv.h5ad 不存在；"
                 "先跑 st_deconvolve")
            raise SystemExit(1)
        import anndata as ad
        dec: Any = ad.read_h5ad(p)  # stub 返回 Any | Dataset2D，标 Any 收窄
        common = adata.obs_names.intersection(dec.obs_names)
        if len(common) < 100:
            fail("INVALID_INPUT",
                 f"deconv 与 processed 共有 spot 过少: {len(common)} (<100)")
            raise SystemExit(1)
        weights = dec.obs.loc[common]
        labels = weights.idxmax(axis=1).astype(str)
        return labels.reindex(adata.obs_names), "deconv"
    if annotation_key:
        if annotation_key not in adata.obs:
            fail("INVALID_INPUT",
                 f"annotation_key {annotation_key!r} 不在 obs；可用列: "
                 f"{sorted(str(c) for c in adata.obs.columns)}")
            raise SystemExit(1)
        return adata.obs[annotation_key].astype(str), annotation_key
    for cand in ANNOTATION_FALLBACK:
        if cand in adata.obs:
            return adata.obs[cand].astype(str), cand
    fail("INVALID_INPUT",
         f"无可用注释列（回退链 {ANNOTATION_FALLBACK} 全缺失）；"
         f"可用 obs 列: {sorted(str(c) for c in adata.obs.columns)}")
    raise SystemExit(1)


def _attach_gene_pos(adata: Any) -> Any:
    """按 TSV 定位并基因组序排序基因，返回带 var 三列的新 AnnData。

    upper 归一匹配、同名歧义丢弃（宁可少配不错配）；定位 <1000 报错。
    （与 sc_cnv 同逻辑。）
    """
    pos = pd.read_csv(GENE_POS_TSV, sep="\t")
    upper_to_row: dict[str, pd.Series] = {}
    ambiguous: set[str] = set()
    for _, row in pos.iterrows():
        key = str(row["gene_name"]).upper()
        if key in upper_to_row:
            ambiguous.add(key)
        else:
            upper_to_row[key] = row
    for key in ambiguous:
        upper_to_row.pop(key, None)
    keep: list[str] = []
    chroms: list[str] = []
    starts: list[int] = []
    for g in adata.var_names:
        row = upper_to_row.get(str(g).upper())
        if row is None:
            continue
        keep.append(str(g))
        chroms.append(str(row["chromosome"]))
        starts.append(int(row["start"]))
    if len(keep) < 1000:
        raise ValueError(
            f"too few genes positioned ({len(keep)} < 1000); check gene "
            "naming (symbol vs ensembl) and species (human GRCh38)")
    sub = adata[:, keep].copy()
    sub.var["chromosome"] = pd.Categorical(chroms, categories=CHROM_ORDER)
    sub.var["start"] = np.asarray(starts, dtype=np.int64)
    sub.var["end"] = pos.set_index("gene_name")["end"][
        [str(g) for g in sub.var_names]].to_numpy()
    rank = {c: i for i, c in enumerate(CHROM_ORDER)}
    order = sorted(range(sub.n_vars), key=lambda i: (
        rank[str(sub.var["chromosome"].iloc[i])],
        int(sub.var["start"].iloc[i])))
    sub = sub[:, order].copy()
    return sub


def _run_infercnvpy(cnv_ad: Any, label_col: str,
                    ref_cats: list[str]) -> np.ndarray:
    """infercnvpy 后端：normalize+log1p → tl.infercnv 基因级矩阵。

    探针实测（sc_cnv）：calculate_gene_values=True + inplace=False 返回
    三元组，第三元素与 var 逐列对齐；exclude_chromosomes=() 显式保留
    chrX/Y 保列数一致；n_jobs=2 限并发防 worker 内存叠加。
    """
    import infercnvpy as cnv
    import scanpy as sc
    from scipy import sparse

    sc.pp.normalize_total(cnv_ad, target_sum=1e4)
    sc.pp.log1p(cnv_ad)
    cnv_ad.X = cnv_ad.X.astype(np.float32)  # 稠密化内存减半
    result = cnv.tl.infercnv(
        cnv_ad,
        reference_key=label_col,
        reference_cat=ref_cats,
        window_size=250,
        exclude_chromosomes=(),
        calculate_gene_values=True,
        n_jobs=2,  # 限并发防 worker 内存叠加（容器 --cpus 4/--memory 16g）
        inplace=False,
    )
    gene_values = result[2]
    if sparse.issparse(gene_values):
        gene_values = gene_values.toarray()
    return np.asarray(gene_values, dtype=np.float32)


def _cell_scores(x_cnv: np.ndarray, ref_mask: np.ndarray) -> np.ndarray:
    """每 spot CNV 负荷：与参考 spot 中心基因级偏离的均值。"""
    ref_mean = np.asarray(
        x_cnv[ref_mask].mean(axis=0), dtype=np.float64).ravel()
    return np.asarray(np.abs(x_cnv - ref_mean).mean(axis=1),
                      dtype=np.float64)


def _leiden_on_cnv(x_cnv: np.ndarray, resolution: float) -> np.ndarray:
    """X_cnv 矩阵上 PCA→邻居→leiden（igraph 参数与 sc_process 同款）。"""
    import anndata as ad
    import scanpy as sc

    tmp = ad.AnnData(X=np.asarray(x_cnv, dtype=np.float32))
    n_comps = max(2, min(30, tmp.n_vars - 1, tmp.n_obs - 1))
    sc.tl.pca(tmp, n_comps=n_comps, svd_solver="arpack")
    sc.pp.neighbors(tmp, n_neighbors=min(15, tmp.n_obs - 1))
    sc.tl.leiden(tmp, resolution=resolution, flavor="igraph",
                 n_iterations=2, directed=False)
    return np.asarray(tmp.obs["leiden"].astype(str).to_numpy(), dtype=str)


def _chromosome_heatmap(x_cnv: np.ndarray, chrom_col: np.ndarray,
                        ref_mask: np.ndarray, is_mal: np.ndarray,
                        subclone: np.ndarray, png_path: Path) -> None:
    """figS3B 式染色体热图：上=参考 spot，下=恶性 spot 按亚克隆分组。

    色标中心=参考中位数，±1 范围 RdBu_r（与 sc_cnv 同渲染）。
    """
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    rng = np.random.default_rng(0)
    idx_ref = np.where(ref_mask)[0]
    if len(idx_ref) > 800:
        idx_ref = rng.choice(idx_ref, 800, replace=False)
    idx_mal = np.where(is_mal)[0]
    sub_of_mal = np.asarray(subclone)[is_mal]
    rows: list[int] = []
    for c in sorted(set(sub_of_mal.tolist())):
        m = idx_mal[sub_of_mal == c]
        if len(m) > 600:
            m = rng.choice(m, 600, replace=False)
        rows.extend(int(i) for i in m)
    heat = x_cnv[list(idx_ref) + rows]
    center = float(np.median(x_cnv[ref_mask]))
    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(heat, aspect="auto", cmap="RdBu_r",
                   interpolation="nearest",
                   norm=mcolors.TwoSlopeNorm(vcenter=center,
                                             vmin=center - 1.0,
                                             vmax=center + 1.0))
    present = [c for c in CHROM_ORDER if (chrom_col == c).any()]
    bounds = np.cumsum([0] + [int((chrom_col == c).sum()) for c in present])
    for b in bounds[1:-1]:
        ax.axvline(b - 0.5, color="black", lw=0.4)
    ax.set_xticks((bounds[:-1] + bounds[1:]) / 2)
    ax.set_xticklabels(present, fontsize=7)
    ax.set_yticks([])
    ax.axhline(len(idx_ref) - 0.5, color="black", lw=1.2)
    fig.colorbar(im, ax=ax, shrink=0.6, label="CNV signal")
    ax.set_title(
        f"CNV chromosome heatmap (infercnvpy); "
        f"top: reference (n={len(idx_ref)}), "
        f"bottom: malignant (n={len(rows)})",
        fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _scatter_img_kwargs(adata: Any) -> dict[str, Any]:
    """spatial_scatter 图像参数（st_stats 同款）：无 uns['spatial'] 补占位
    壳 + img=False；有真实组织图走默认带图。

    容器探针（2026-09-11）：uns 缺 'spatial' → KeyError；空 images 壳
    不指定 img=False → 仍尝试取 hires 报错；两坑同避。
    """
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


def _spatial_pngs(adata: Any, out_dir: Path) -> tuple[Path, Path]:
    """cnv_score 连续着色 + cnv_subclone 分类着色 spatial_scatter 组织图。

    在写回后的 processed 对象上绘制（obsm['spatial'] 由 ensure_spatial
    保证）。return_ax=True（T7 教训）。
    """
    import matplotlib.pyplot as plt
    import squidpy as sq

    img_kw = _scatter_img_kwargs(adata)
    score_png = out_dir / "cnv_score_spatial.png"
    ax = sq.pl.spatial_scatter(adata, color=["cnv_score"],
                               return_ax=True, **img_kw)
    ax.set_title("CNV score")
    ax.figure.savefig(score_png, dpi=150, bbox_inches="tight")
    plt.close("all")
    sub_png = out_dir / "cnv_subclone_spatial.png"
    ax = sq.pl.spatial_scatter(adata, color=["cnv_subclone"],
                               return_ax=True, **img_kw)
    ax.set_title("CNV subclone")
    ax.figure.savefig(sub_png, dpi=150, bbox_inches="tight")
    plt.close("all")
    return score_png, sub_png


def main() -> None:
    """主流程：对齐 → infercnvpy 推断 → 打分/判定/亚克隆 → 写回产物。"""
    args = read_args()
    annotation_key = str(args.get("annotation_key", "")).strip()
    ref_groups = args.get("ref_groups") or None
    resolution = float(args.get("resolution", 1.0))

    counts_ad = load_adata({"dataset_id": args["dataset_id"], "file": "any"})
    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    labels, key_desc = _resolve_labels(adata, args["dataset_id"],
                                       annotation_key)

    common = adata.obs_names.intersection(counts_ad.obs_names)
    if len(common) < 100:
        fail("INVALID_INPUT",
             f"counts 与 processed 共有 spot 过少: {len(common)} (<100)")
        raise SystemExit(1)

    values = sorted(set(labels.dropna().astype(str)))
    try:
        ref_cats = _match_references(values, ref_groups)
    except NoReferenceError as e:
        fail("ST_CNV_NO_REFERENCE", str(e))
        raise SystemExit(1)

    # counts 子集 + 标签对齐（sc_cnv/doublet.py 先例）
    cnv_ad = counts_ad[common].copy()
    cnv_ad.obs["cnv_label"] = labels.loc[common].astype(str).to_numpy()
    n_ref = int(cnv_ad.obs["cnv_label"].isin(ref_cats).sum())
    if n_ref < 30:
        fail("INVALID_INPUT",
             f"参考 spot 过少 ({n_ref} < 30)；检查 ref 选择")
        raise SystemExit(1)

    genes_total = int(cnv_ad.n_vars)
    # 基因表达预过滤（R inferCNV cutoff=0.1 语义：平均 counts≥0.1 才进
    # 推断）：把稠密矩阵规模压进容器 16g 限额（2026-09-11 sc_cnv 真机
    # OOM 教训：joblib worker 被杀 → BrokenProcessPool）
    mean_counts = np.asarray(cnv_ad.X.mean(axis=0)).ravel()
    keep = mean_counts >= 0.1
    if int(keep.sum()) < 1000:
        raise ValueError(
            f"too few expressed genes after count cutoff 0.1: "
            f"{int(keep.sum())} (<1000)")
    cnv_ad = cnv_ad[:, keep].copy()
    genes_expressed = int(cnv_ad.n_vars)
    cnv_ad = _attach_gene_pos(cnv_ad)
    n_positioned = int(cnv_ad.n_vars)

    x_cnv = _run_infercnvpy(cnv_ad, "cnv_label", ref_cats)
    chrom_col = np.asarray(
        cnv_ad.var["chromosome"].astype(str).to_numpy(), dtype=str)
    if x_cnv.shape[1] != len(chrom_col):
        raise ValueError(
            f"X_cnv columns ({x_cnv.shape[1]}) != expected genes "
            f"({len(chrom_col)}); backend var tracking changed")

    # NaN 防御列剔除（sc_cnv 真机实测 infercnv 窗口边界输出带 NaN，
    # 下游 PCA 拒绝）；chrom_col 同步对齐
    finite = np.isfinite(x_cnv).all(axis=0)
    if not bool(finite.all()):
        bad_chroms = sorted(set(chrom_col[~finite].tolist()))
        x_cnv = np.ascontiguousarray(x_cnv[:, finite])
        chrom_col = chrom_col[finite]
        note_nan = (f"；剔除 NaN 基因列 {int((~finite).sum())} 个"
                    f"（涉及 {'/'.join(bad_chroms)}）")
    else:
        note_nan = ""
    if x_cnv.shape[1] < 100 or not bool(np.isfinite(x_cnv).all()):
        raise ValueError(
            f"X_cnv 有效基因列过少或仍含非有限值（{x_cnv.shape}），"
            "推断矩阵异常")

    ref_mask = cnv_ad.obs["cnv_label"].isin(ref_cats).to_numpy(dtype=bool)
    scores = _cell_scores(x_cnv, ref_mask)
    ref_scores = scores[ref_mask]
    threshold = float(ref_scores.mean() + 3.0 * ref_scores.std())

    # infercnvpy：spot 级阈值 → CNV 簇多数投票平滑（簇级判定更稳）
    clusters = _leiden_on_cnv(x_cnv, 0.5)
    raw_flag = scores > threshold
    is_mal = np.zeros(len(scores), dtype=bool)
    for c in set(clusters):
        m = clusters == c
        is_mal[m] = raw_flag[m].mean() > 0.5
    note = ("infercnvpy 阈值 + CNV 簇多数投票（阈值=参考分 mean+3sd）"
            + note_nan)

    subclone = np.full(len(scores), "non-malignant", dtype=object)
    n_mal = int(is_mal.sum())
    if n_mal >= 50:
        mal_leiden = _leiden_on_cnv(x_cnv[is_mal], resolution)
        sizes = pd.Series(mal_leiden).value_counts()
        remap = {c: f"C{i + 1}" for i, c in enumerate(sizes.index)}
        subclone[is_mal] = [remap[c] for c in mal_leiden]
        note += f"；亚克隆 {len(sizes)} 个"
    elif n_mal > 0:
        subclone[is_mal] = "C1"
        note += "；恶性 spot <50 退化为单克隆 C1"

    out_dir = WS_ROOT / args["dataset_id"] / "cnv"
    out_dir.mkdir(parents=True, exist_ok=True)

    lb_series = cnv_ad.obs["cnv_label"].astype(str)
    summary = pd.crosstab(lb_series,
                          pd.Series(is_mal, index=lb_series.index))
    summary_csv = out_dir / "cnv_label_summary.csv"
    summary.to_csv(summary_csv)
    malignant_by_label = {str(k): int(v) for k, v in
                          lb_series[np.asarray(is_mal)]
                          .value_counts().head(10).items()}

    ref_mean = np.asarray(x_cnv[ref_mask].mean(axis=0),
                          dtype=np.float64).ravel()
    dev = x_cnv - ref_mean
    sub_s = pd.Series(subclone)
    sub_labels = [c for c in sub_s.value_counts().index
                  if c != "non-malignant"]
    chr_rows: dict[str, dict[str, float]] = {}
    for label in sub_labels:
        m = (sub_s == label).to_numpy()
        chr_rows[label] = {
            c: round(float(dev[m][:, chrom_col == c].mean()), 4)
            for c in CHROM_ORDER if (chrom_col == c).any()}
    chr_csv = out_dir / "cnv_subclone_by_chromosome.csv"
    pd.DataFrame(chr_rows).T.to_csv(chr_csv, index_label="subclone")

    heatmap_png = out_dir / "cnv_chromosome_heatmap.png"
    _chromosome_heatmap(x_cnv, chrom_col, ref_mask, is_mal, subclone,
                        heatmap_png)

    # 写回 processed.h5ad（reindex 对齐；st_plot 可作着色列）
    adata.obs["cnv_score"] = pd.Series(
        scores, index=cnv_ad.obs_names).reindex(adata.obs_names)
    adata.obs["is_malignant"] = pd.Series(
        is_mal, index=cnv_ad.obs_names).reindex(adata.obs_names)
    adata.obs["cnv_subclone"] = pd.Series(
        subclone, index=cnv_ad.obs_names).reindex(adata.obs_names)
    score_png, sub_png = _spatial_pngs(adata, out_dir)
    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": "infercnvpy",
        "annotation_key": key_desc,
        "matched_references": ref_cats,
        "annotation_values": values,
        "n_reference_spots": n_ref,
        "n_spots": int(len(common)),
        "genes": {"total": genes_total, "expressed": genes_expressed,
                  "positioned": n_positioned},
        "n_malignant": n_mal,
        "malignant_ratio": round(n_mal / max(1, len(common)), 4),
        "threshold": round(threshold, 4),
        "n_subclones": len(sub_labels),
        "subclone_sizes": {c: int((subclone == c).sum())
                           for c in sub_labels},
        "malignant_by_label": malignant_by_label,
        # pngs 聚合键：IM 发图与 D 报告共用宿主四键收集（umap/dotplot/
        # spatial/pngs），2026-09-11 sc_cnv 验收漏图教训
        "pngs": [str(heatmap_png), str(score_png), str(sub_png)],
        "chromosome_heatmap_png": str(heatmap_png),
        "score_spatial_png": str(score_png),
        "subclone_spatial_png": str(sub_png),
        "label_summary_csv": str(summary_csv),
        "subclone_by_chromosome_csv": str(chr_csv),
        "saved": str(h5ad_path),
        "note": note + "；is_malignant/cnv_subclone 已写回 processed.h5ad，"
                       "st_plot 可作 color_by 分组列",
    })


if __name__ == "__main__":
    run(main)
