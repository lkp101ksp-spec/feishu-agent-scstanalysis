"""sc_milo：差异丰度分析（Phase 34，Python 复刻 miloR 思路）。

stdin: {"dataset_id": ..., "sample_col": "sample", "group_col": "condition",
        "group_a": "treated", "group_b": "control", "k": 0, "top_n": 20,
        "max_cells_per_sample": 0}
需 processed.h5ad。流程：KNN 图（复用 connectivities，缺则重建）→
refined 式邻域采样（与已留邻域重叠>0.8 的种子跳过）→ 样本×邻域计数 →
逐邻域 QP-GLM（Poisson + 全局 Pearson 离散度，offset=log 样本总细胞数）→ BH 校正。
口径声明：非 edgeR 准似然模型；BH 非 miloR SpatialFDR 加权校正——
结果为近似口径，离散度 floor=1 欠离散不回缩保持偏保守，详见 emit 的 method_note。
重复单位：GLM 拟合在 样本×邻域 计数上（sample_col 为重复单位），
天然样本级无细胞级伪重复——与 sc_de/sc_cellfreq 的 donor_col
供体级并列口径互补（2026-09-16 钉注）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata: Any) -> str:
    """列出可作分组的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    """两邻域（升序唯一索引数组）重叠度：交集 / 较小集合。"""
    inter = np.intersect1d(a, b, assume_unique=True).size
    return inter / max(1, min(a.size, b.size))


def main() -> None:
    """主流程：邻域采样 → 逐邻域 NB-GLM → BH → da csv + UMAP 着色图。"""
    import matplotlib.pyplot as plt
    import scanpy as sc
    import statsmodels.api as sm
    from scipy import stats as sps
    from statsmodels.stats.multitest import multipletests

    args = read_args()
    sample_col = str(args.get("sample_col", "")).strip()
    group_col = str(args.get("group_col", "")).strip()
    group_a = str(args.get("group_a", "")).strip()
    group_b = str(args.get("group_b", "")).strip()
    if not (sample_col and group_col and group_a and group_b):
        raise ValueError(
            "sample_col/group_col/group_a/group_b are all required, e.g. "
            "{'sample_col': 'sample', 'group_col': 'condition', "
            "'group_a': 'treated', 'group_b': 'control'}")
    k_arg = int(args.get("k", 0))
    top_n = int(args.get("top_n", 20))
    max_cells_per_sample = int(args.get("max_cells_per_sample", 0))

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    for col in (sample_col, group_col):
        if col not in adata.obs:
            raise ValueError(
                f"column {col!r} not in obs; available: {_cat_cols(adata)}")
    samples = adata.obs[sample_col].astype(str)
    grp_per_sample = adata.obs.groupby(samples)[group_col].agg(
        lambda s: s.astype(str).unique().tolist())
    bad = grp_per_sample[grp_per_sample.map(len) > 1]
    if not bad.empty:
        raise ValueError(
            f"samples with mixed {group_col} values: {bad.index.tolist()[:10]}")
    sample_group = grp_per_sample.map(lambda v: v[0])
    for g in (group_a, group_b):
        if g not in set(sample_group):
            raise ValueError(
                f"group value {g!r} not found per-sample; existing: "
                f"{sorted(set(sample_group))[:20]}")
    n_sa = int((sample_group == group_a).sum())
    n_sb = int((sample_group == group_b).sum())
    if n_sa < 2 or n_sb < 2:
        raise ValueError(
            f"need >=2 samples per group: {group_a}={n_sa}, {group_b}={n_sb}")

    # 分层抽样（可选）：按样本列（保留样本复制结构，milo 统计必需），
    # 每样本取 min(max_cells_per_sample, 样本细胞数)，
    # 固定 random_state=42 保证可复现；0=全量。
    n_cells_total = adata.n_obs
    if max_cells_per_sample > 0:
        idx: list[Any] = []
        for _, g in adata.obs.groupby(sample_col):
            idx.extend(g.sample(n=min(max_cells_per_sample, len(g)),
                                random_state=42).index)
        adata = adata[idx].copy()
        samples = adata.obs[sample_col].astype(str)
    subsampled = adata.n_obs < n_cells_total

    # KNN 图：优先复用 sc_process 的 connectivities
    if "connectivities" not in adata.obsp:
        sc.pp.neighbors(adata, n_neighbors=15)
    conn = adata.obsp["connectivities"].tocsr()

    n_cells = adata.n_obs
    if k_arg > 0:
        k = k_arg
    else:
        k = int(np.clip(round(0.1 * samples.value_counts().min()), 10, 50))

    # refined 式邻域采样：随机序种子，重叠>0.8 跳过
    rng = np.random.default_rng(42)
    kept: list[int] = []
    kept_nbrs: list[np.ndarray] = []
    for i in rng.permutation(n_cells):
        nbr = np.unique(
            np.append(conn.indices[conn.indptr[i]:conn.indptr[i + 1]], i))
        if nbr.size < 3:
            continue
        if any(_overlap(nbr, kn) > 0.8 for kn in kept_nbrs):
            continue
        kept.append(int(i))
        kept_nbrs.append(nbr)
    if len(kept) < 5:
        raise ValueError(f"too few neighbourhoods sampled: {len(kept)}")

    # 样本×邻域计数
    sample_cats = pd.Categorical(samples)
    codes = sample_cats.codes
    sample_names = sample_cats.categories.tolist()
    counts = np.zeros((len(kept), len(sample_names)), dtype=int)
    for row, nbr in enumerate(kept_nbrs):
        counts[row] = np.bincount(codes[nbr], minlength=len(sample_names))

    # 逐邻域 QP-GLM：count ~ group，offset=log(样本总细胞数)。
    # 固定 alpha=1 的 NB 在 2v2 小样本次数下 SE≈1、Wald 检验无功效；
    # 改为 Poisson 拟合 + 全局 Pearson 离散度（median chi2/df，floor=1），
    # 数据驱动离散度更接近 miloR edgeR QL 思路，欠离散不回缩保持偏保守。
    totals = samples.value_counts().reindex(sample_names).to_numpy()
    x = sample_group.reindex(sample_names).map(
        {group_b: 0.0, group_a: 1.0}).to_numpy()
    X = sm.add_constant(x)
    offset = np.log(totals.astype(float))
    logfc = np.full(len(kept), np.nan)
    pvals = np.full(len(kept), np.nan)
    beta = np.full(len(kept), np.nan)
    se = np.full(len(kept), np.nan)
    chi2 = np.full(len(kept), np.nan)
    for row in range(len(kept)):
        try:
            fit = sm.GLM(counts[row], X, offset=offset,
                         family=sm.families.Poisson()).fit()
            beta[row] = fit.params[1]
            se[row] = fit.bse[1]
            chi2[row] = fit.pearson_chi2 / max(float(fit.df_resid), 1.0)
        except Exception:  # noqa: BLE001 —— 单邻域拟合失败置 nan，BH 跳过
            continue
    ok = ~np.isnan(beta)
    scale = max(1.0, float(np.nanmedian(chi2)))
    logfc[ok] = beta[ok] / np.log(2)
    z = beta[ok] / (se[ok] * np.sqrt(scale))
    pvals[ok] = 2 * sps.norm.sf(np.abs(z))
    fdr = np.full(len(kept), np.nan)
    fdr[ok] = multipletests(pvals[ok], method="fdr_bh")[1]

    # 邻域注释：多数 leiden 标签与构成比
    labels = (adata.obs["leiden"].astype(str)
              if "leiden" in adata.obs
              else pd.Series(["NA"] * n_cells))
    maj, maj_frac = [], []
    for nbr in kept_nbrs:
        vc = labels.iloc[nbr].value_counts()
        maj.append(str(vc.index[0]))
        maj_frac.append(round(float(vc.iloc[0]) / nbr.size, 3))

    df = pd.DataFrame({
        "nhood": range(len(kept)),
        "index_cell": kept,
        "n_cells": [n.size for n in kept_nbrs],
        "log2fc": logfc,
        "pval": pvals,
        "fdr": fdr,
        "majority_label": maj,
        "majority_frac": maj_frac,
    }).sort_values("fdr")

    out_dir = WS_ROOT / args["dataset_id"] / "milo"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"milo_{group_a}_vs_{group_b}.csv"
    df.to_csv(csv_path, index=False)

    # UMAP：灰底 + 种子细胞按 log2fc 着色（FDR<0.1 加黑边）
    fig, ax = plt.subplots(figsize=(6, 5))
    umap = adata.obsm["X_umap"]
    ax.scatter(umap[:, 0], umap[:, 1], s=4, c="#d8d8d8", linewidths=0)
    vmax = float(np.nanmax(np.abs(logfc))) or 1.0
    seed_xy = umap[np.array(kept)]
    sc_plot = ax.scatter(seed_xy[:, 0], seed_xy[:, 1], s=18, c=logfc,
                         cmap="RdBu_r", vmin=-vmax, vmax=vmax,
                         linewidths=0)
    sig_mask = fdr < 0.1
    if sig_mask.any():
        ax.scatter(seed_xy[sig_mask, 0], seed_xy[sig_mask, 1], s=26,
                   facecolors="none", edgecolors="black", linewidths=0.8)
    fig.colorbar(sc_plot, ax=ax, shrink=0.8, label="log2FC")
    ax.set_title(f"milo DA: {group_a}(n={n_sa}) vs {group_b}(n={n_sb}), "
                 f"k={k}, {len(kept)} nhoods", fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    png_path = out_dir / f"milo_{group_a}_vs_{group_b}_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top = [
        {"nhood": int(r["nhood"]), "log2fc": round(float(r["log2fc"]), 2),
         "fdr": float(f"{r['fdr']:.2e}"),
         "majority_label": str(r["majority_label"]),
         "n_cells": int(r["n_cells"])}
        for _, r in df.head(top_n).iterrows()
        if not np.isnan(r["fdr"])]

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "comparison": f"{group_a}_vs_{group_b}",
        "sample_col": sample_col, "group_col": group_col,
        "n_samples_a": n_sa, "n_samples_b": n_sb,
        "n_cells_used": int(adata.n_obs),
        "subsampled": subsampled,
        "k": k, "n_nhoods": len(kept),
        "n_sig_fdr01": int((fdr < 0.1).sum()),
        "dispersion_scale": round(scale, 3),
        "top": top,
        "csv": str(csv_path),
        "umap_png": str(png_path),
        "method_note": ("Python 复刻：QP-GLM(Poisson+全局 Pearson 离散度 "
                        "floor=1)+BH；非 miloR edgeR QL 模型与 SpatialFDR "
                        "加权校正，显著性口径偏保守"
                        + (f"；按 {sample_col} 分层抽样：每样本最多 "
                           f"{max_cells_per_sample} 细胞"
                           f"（{n_cells_total}→{adata.n_obs}）"
                           if subsampled else "")),
    })


if __name__ == "__main__":
    run(main)
