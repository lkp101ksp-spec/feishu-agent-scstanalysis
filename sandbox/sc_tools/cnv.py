"""sc_cnv：拷贝数变异推断与恶性判定（B1，双后端 infercnvpy/cnvturbo）。

stdin: {"dataset_id": ..., "method": "infercnvpy"|"cnvturbo",
        "celltype_col": "leiden",          # 参考细胞来源列（注释列亦可）
        "ref_groups": ["T cells", ...],    # 显式参考；缺省内置清单匹配
        "resolution": 1.0}                 # 亚克隆 leiden 分辨率
数据流（doublet.py 先例）：counts 走 filtered/raw 回退链（全基因矩阵），
标签/UMAP 走 processed.h5ad 按 obs_names 交集对齐；两后端同源起步
（infercnvpy 侧自 normalize+log1p，cnvturbo 侧吃原始 counts）保证交叉
验证可比。结果 reindex 写回 processed.h5ad：cnv_score / is_malignant /
（仅 cnvturbo）cnv_call / cnv_subclone。R 兼容参数（window=101、
cutoff=0.1、2x transform、排除 chrX/Y）内部定死不暴露。
产物落 /ws/{ds}/cnv/：figS3B 式染色体热图（双后端统一自绘）、
score/subclone UMAP、注释×恶性计数 csv、亚克隆×染色体 csv。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run

GENE_POS_TSV = "/opt/cnv/gene_pos_grch38.tsv"
CHROM_ORDER = [f"chr{i}" for i in range(1, 23)] + ["chrX", "chrY"]
# 人源常见非恶性参考（大小写不敏感子串匹配，"T cell" 命中 "CD4 T cells"）。
# Epithelial 故意不在默认清单（可为恶性来源），靠 ref_groups 显式加。
DEFAULT_REF_PATTERNS = [
    "T cell", "B cell", "NK", "Macrophage", "Monocyte", "Dendritic",
    "Neutrophil", "Fibroblast", "Endothelial", "Pericyte",
    "Smooth muscle", "Erythrocyte",
]


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
            f'ref_groups explicitly, e.g. ["T cells", "Fibroblast"]; '
            f"available values: {values}")
    return matched


def _attach_gene_pos(adata: Any) -> Any:
    """按 TSV 定位并基因组序排序基因，返回带 var 三列的新 AnnData。

    upper 归一匹配、同名歧义丢弃（宁可少配不错配）；定位 <1000 报错。
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


def _run_infercnvpy(cnv_ad: Any, celltype_col: str,
                    ref_cats: list[str]) -> np.ndarray:
    """infercnvpy 后端：normalize+log1p（其文档惯例）→ tl.infercnv。

    探针实测修正：tl.infercnv 默认写入 obsm["X_cnv"] 的是窗口级矩阵
    （列不与 var 对齐）；calculate_gene_values=True + inplace=False 时
    返回三元组 (chr_pos, X_cnv, gene_values)，第三元素为与 var 逐列
    对齐的基因级矩阵，取之作 CNV 矩阵。exclude_chromosomes=() 显式
    保留 chrX/Y（库默认排除），保证列数与定位后 var 一一对应。
    """
    import infercnvpy as cnv
    import scanpy as sc
    from scipy import sparse

    sc.pp.normalize_total(cnv_ad, target_sum=1e4)
    sc.pp.log1p(cnv_ad)
    cnv_ad.X = cnv_ad.X.astype(np.float32)  # 稠密化内存减半
    result = cnv.tl.infercnv(
        cnv_ad,
        reference_key=celltype_col,
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


def _run_cnvturbo(cnv_ad: Any, celltype_col: str,
                  ref_cats: list[str]
                  ) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """cnvturbo 后端：R 兼容主推断 + hspike 校准 + HMM i6 恶性判定。

    counts 塞进 layers["counts"]（raw_layer 语义）；返回 (X_cnv,
    is_malignant, kept_var_names)，is_malignant = HMM 判定 Tumor。
    探针实测修正：① infercnv_r_compat 参数名 exclude_chromosomes
    （正常拼写），显式排 chrX/Y；② hspike 返回 4 元组
    (emit_means, emit_stds, sd_intercepts, sd_slopes)，位置解包；
    ③ hmm_call_subclusters 预计算参数带 emit_ 前缀；④ 保列基因
    清单取 uns["cnv"]["kept_var_names"]（与 X_cnv 列一一对应），
    恶性判定读 obs["cnv_call"]（"Tumor"/"Normal"）。
    """
    from cnvturbo import tl as ct_tl
    from scipy import sparse

    cnv_ad.layers["counts"] = cnv_ad.X.copy().astype(np.float32)  # 内存减半
    ct_tl.infercnv_r_compat(
        cnv_ad,
        raw_layer="counts",
        reference_key=celltype_col,
        reference_cat=ref_cats,
        window_size=101,
        min_mean_expr_cutoff=0.1,
        exclude_chromosomes=("chrX", "chrY"),
        apply_2x_transform=True,
        n_jobs=2,
        key_added="cnv",
    )
    emit_means, emit_stds, sd_intercepts, sd_slopes = (
        ct_tl.compute_hspike_emission_params(
            cnv_ad,
            raw_layer="counts",
            reference_key=celltype_col,
            reference_cat=ref_cats,
            min_mean_expr_cutoff=0.1,
            n_sim_cells=100,
            n_genes_per_chr=400,
            output_space="copy_ratio",
            return_sd_trend=True,
        )
    )
    ct_tl.hmm_call_subclusters(
        cnv_ad,
        use_rep="cnv",
        reference_key=celltype_col,
        reference_cat=ref_cats,
        precomputed_emit_means=emit_means,
        precomputed_emit_stds=emit_stds,
        precomputed_emit_sd_intercepts=sd_intercepts,
        precomputed_emit_sd_slopes=sd_slopes,
        leiden_resolution="auto",
        cluster_by_groups=True,
        min_segment_length=5,
        # 口径评估（2026-09-12，bio_workspace/_eval）：humantest 19149
        # 细胞上 ms=1/2/3 判定完全一致（肿瘤区段普遍 ≥3，旋钮不敏感），
        # 与 infercnvpy 基线 Jaccard 0.589、覆盖基线 96.8%、分型边界
        # 干净零误报——双后端分歧源于细胞级 HMM vs 簇级投票的判定
        # 逻辑差异，非本参数；维持 1（R inferCNV 默认语义）。
        min_segments_for_tumor=1,
        key_added="cnv_call",
        n_jobs=2,
    )
    kept = [str(g) for g in cnv_ad.uns["cnv"]["kept_var_names"]]
    x_cnv = cnv_ad.obsm["X_cnv"]
    if sparse.issparse(x_cnv):
        x_cnv = x_cnv.toarray()
    calls = cnv_ad.obs["cnv_call"].astype(str).to_numpy()
    is_mal = np.asarray(calls == "Tumor", dtype=bool)
    return np.asarray(x_cnv, dtype=np.float32), is_mal, kept


def _cell_scores(x_cnv: np.ndarray, ref_mask: np.ndarray) -> np.ndarray:
    """每细胞 CNV 负荷：与参考细胞中心基因级偏离的均值。"""
    ref_mean = np.asarray(
        x_cnv[ref_mask].mean(axis=0), dtype=np.float64).ravel()
    return np.asarray(np.abs(x_cnv - ref_mean).mean(axis=1),
                      dtype=np.float64)


def _leiden_on_cnv(x_cnv: np.ndarray, resolution: float) -> np.ndarray:
    """X_cnv 矩阵上 PCA→邻居→leiden（与 sc_process 同款 igraph 参数）。"""
    import anndata as ad
    import scanpy as sc

    tmp = ad.AnnData(X=np.asarray(x_cnv, dtype=np.float32))
    n_comps = max(2, min(30, tmp.n_vars - 1, tmp.n_obs - 1))
    sc.tl.pca(tmp, n_comps=n_comps, svd_solver="arpack")
    sc.pp.neighbors(tmp, n_neighbors=min(15, tmp.n_obs - 1))
    sc.tl.leiden(tmp, resolution=resolution, flavor="igraph",
                 n_iterations=2, directed=False)
    return np.asarray(tmp.obs["leiden"].astype(str).to_numpy(), dtype=str)


def _umap_score_png(umap: np.ndarray, scores: np.ndarray,
                    png_path: Path) -> None:
    """UMAP 连续着色（cnv_score）。"""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 5))
    sc_ = ax.scatter(umap[:, 0], umap[:, 1], s=5, c=scores,
                     cmap="viridis", linewidths=0)
    fig.colorbar(sc_, ax=ax, shrink=0.8, label="cnv_score")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("CNV score", fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _umap_subclone_png(umap: np.ndarray, subclone: np.ndarray,
                       png_path: Path) -> None:
    """UMAP 分类着色（cnv_subclone，tab20 循环）。"""
    import matplotlib.pyplot as plt

    cats = sorted(set(str(s) for s in subclone))
    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(7, 5.5))
    for i, c in enumerate(cats):
        m = np.asarray([str(s) == c for s in subclone])
        ax.scatter(umap[m, 0], umap[m, 1], s=5, color=cmap(i % 20),
                   label=f"{c} ({int(m.sum())})", linewidths=0)
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.0, 0.5),
              markerscale=2)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("CNV subclone", fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _chromosome_heatmap(x_cnv: np.ndarray, chrom_col: np.ndarray,
                        ref_mask: np.ndarray, is_mal: np.ndarray,
                        subclone: np.ndarray, png_path: Path,
                        method: str) -> None:
    """figS3B 式染色体热图：上=参考细胞，下=恶性细胞按亚克隆分组。

    双后端统一渲染（色标中心=参考细胞中位数，±1 范围 RdBu_r）。
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
        f"CNV chromosome heatmap ({method}); "
        f"top: reference (n={len(idx_ref)}), "
        f"bottom: malignant (n={len(rows)})",
        fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    """主流程：对齐 → 双后端之一推断 → 打分/判定/亚克隆 → 写回产物。"""
    args = read_args()
    method = str(args.get("method", "infercnvpy")).strip().lower()
    if method not in ("infercnvpy", "cnvturbo"):
        raise ValueError(
            f"method must be infercnvpy or cnvturbo, got {method!r}")
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    ref_groups = args.get("ref_groups") or None
    resolution = float(args.get("resolution", 1.0))

    counts_ad = load_adata({"dataset_id": args["dataset_id"], "file": "any"})
    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    if celltype_col not in adata.obs:
        raise ValueError(
            f"celltype column {celltype_col!r} not in obs; available: "
            f"{sorted(str(c) for c in adata.obs.columns)}")
    if "X_umap" not in adata.obsm:
        raise ValueError("processed.h5ad missing X_umap; re-run sc_process")
    common_cells = adata.obs_names.intersection(counts_ad.obs_names)
    if len(common_cells) < 200:
        raise ValueError(
            f"too few cells shared between counts and processed: "
            f"{len(common_cells)} (<200)")

    values = sorted(set(adata.obs[celltype_col].astype(str)))
    try:
        ref_cats = _match_references(values, ref_groups)
    except NoReferenceError as e:
        fail("SC_CNV_NO_REFERENCE", str(e))
        return

    # 双后端同源起步：同一 counts 子集 + 标签对齐（doublet.py 先例）
    cnv_ad = counts_ad[common_cells].copy()
    cnv_ad.obs[celltype_col] = (
        adata.obs.loc[common_cells, celltype_col].astype(str).to_numpy())
    n_ref = int(cnv_ad.obs[celltype_col].isin(ref_cats).sum())
    if n_ref < 50:
        raise ValueError(f"too few reference cells ({n_ref} < 50); "
                         "check ref choice")

    genes_total = int(cnv_ad.n_vars)
    # 基因表达预过滤（R inferCNV cutoff=0.1 语义：平均 counts≥0.1 才进
    # 推断）：双后端同源口径，并把稠密矩阵规模压进容器 16g 限额
    # （2026-09-11 真机验收：20533×31884 float64 多份拷贝 OOM，joblib
    #  worker 被杀 → BrokenProcessPool）
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

    kept_genes: list[str] | None = None
    if method == "infercnvpy":
        x_cnv = _run_infercnvpy(cnv_ad, celltype_col, ref_cats)
        turbo_calls: np.ndarray | None = None
    else:
        x_cnv, turbo_calls, kept_genes = _run_cnvturbo(
            cnv_ad, celltype_col, ref_cats)

    # 染色体列与 X_cnv 列对齐：infercnvpy 基因级矩阵与 var 逐列对齐；
    # cnvturbo 主推断按 exclude_chromosomes 剔列，保列清单以
    # uns["cnv"]["kept_var_names"] 实测契约为准（探针修正④）。
    if kept_genes is not None:
        chrom_col = np.asarray(
            cnv_ad.var.loc[kept_genes, "chromosome"]
            .astype(str).to_numpy(), dtype=str)
    else:
        chrom_col = np.asarray(
            cnv_ad.var["chromosome"].astype(str).to_numpy(), dtype=str)
    if x_cnv.shape[1] != len(chrom_col):
        raise ValueError(
            f"X_cnv columns ({x_cnv.shape[1]}) != expected genes "
            f"({len(chrom_col)}); backend var tracking changed")

    # NaN 防御列剔除：基因过少染色体/窗口边界的 infercnv 输出会带 NaN
    # 列（2026-09-11 真机实测，下游 PCA 拒绝 NaN）；chrom_col 同步对齐
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

    ref_mask = cnv_ad.obs[celltype_col].isin(ref_cats).to_numpy(dtype=bool)
    scores = _cell_scores(x_cnv, ref_mask)
    ref_scores = scores[ref_mask]
    threshold = float(ref_scores.mean() + 3.0 * ref_scores.std())

    note = note_nan
    if turbo_calls is not None:
        is_mal = turbo_calls
        note = "cnvturbo HMM i6 细胞级判定"
    else:
        # infercnvpy：细胞级阈值 → CNV 簇多数投票平滑（簇级判定更稳）
        clusters = _leiden_on_cnv(x_cnv, 0.5)
        raw_flag = scores > threshold
        is_mal = np.zeros(len(scores), dtype=bool)
        for c in set(clusters):
            m = clusters == c
            is_mal[m] = raw_flag[m].mean() > 0.5
        note = "infercnvpy 阈值 + CNV 簇多数投票（阈值=参考分 mean+3sd）"

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
        note += "；恶性细胞 <50 退化为单克隆 C1"

    out_dir = WS_ROOT / args["dataset_id"] / "cnv"
    out_dir.mkdir(parents=True, exist_ok=True)

    ct_series = cnv_ad.obs[celltype_col].astype(str)
    summary = pd.crosstab(ct_series,
                          pd.Series(is_mal, index=ct_series.index))
    summary_csv = out_dir / "cnv_celltype_summary.csv"
    summary.to_csv(summary_csv)
    malignant_by_ct = {str(k): int(v) for k, v in
                       ct_series[np.asarray(is_mal)]
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
                        heatmap_png, method)
    umap = adata.obsm["X_umap"]
    umap_s = pd.Series(scores, index=cnv_ad.obs_names).reindex(
        adata.obs_names).to_numpy(dtype=float)
    sub_s_full = pd.Series(subclone, index=cnv_ad.obs_names).reindex(
        adata.obs_names).astype(str).to_numpy()
    score_png = out_dir / "cnv_score_umap.png"
    _umap_score_png(np.asarray(umap), umap_s, score_png)
    sub_png = out_dir / "cnv_subclone_umap.png"
    _umap_subclone_png(np.asarray(umap), sub_s_full, sub_png)

    adata.obs["cnv_score"] = pd.Series(
        scores, index=cnv_ad.obs_names).reindex(adata.obs_names)
    adata.obs["is_malignant"] = pd.Series(
        is_mal, index=cnv_ad.obs_names).reindex(adata.obs_names)
    adata.obs["cnv_subclone"] = pd.Series(
        subclone, index=cnv_ad.obs_names).reindex(adata.obs_names)
    if turbo_calls is not None:
        adata.obs["cnv_call"] = pd.Series(
            cnv_ad.obs["cnv_call"].astype(str).to_numpy(),
            index=cnv_ad.obs_names).reindex(adata.obs_names)
    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        "matched_references": ref_cats,
        "annotation_values": values,
        "n_reference_cells": n_ref,
        "n_cells": int(len(common_cells)),
        "genes": {"total": genes_total, "expressed": genes_expressed,
                  "positioned": n_positioned},
        "n_malignant": n_mal,
        "malignant_ratio": round(n_mal / max(1, len(common_cells)), 4),
        "threshold": round(threshold, 4),
        "n_subclones": len(sub_labels),
        "subclone_sizes": {c: int((subclone == c).sum())
                           for c in sub_labels},
        "malignant_by_celltype": malignant_by_ct,
        # pngs 聚合键：IM 发图与 D 报告共用宿主四键收集（umap/dotplot/
        # spatial/pngs），2026-09-11 验收发现单名键导致图漏收
        "pngs": [str(heatmap_png), str(score_png), str(sub_png)],
        "chromosome_heatmap_png": str(heatmap_png),
        "score_umap_png": str(score_png),
        "subclone_umap_png": str(sub_png),
        "celltype_summary_csv": str(summary_csv),
        "subclone_by_chromosome_csv": str(chr_csv),
        "saved": str(h5ad_path),
        "note": note + "；is_malignant/cnv_subclone 已写回 processed.h5ad，"
                       "sc_plot/sc_de 可作分组列",
    })


if __name__ == "__main__":
    run(main)
