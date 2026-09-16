"""sc_de：组间差异分析（Phase 33，对齐 toolsv1 server_differential_analysis）。

stdin: {"dataset_id": ..., "groupby": "condition",
        "group_a": "treated", "group_b": "control",
        "method": "wilcoxon", "top_n": 20, "donor_col": ""}
需 processed.h5ad（sc_process 产物）。obs 需含 groupby 列
（源数据自带，如 condition/sample/批次的注释列）。
rank_genes_groups 定向对比 a vs b：上调 = log2fc > 0（a 相对 b）。
donor_col 给定时附加供体级 pseudobulk 检验（2026-09-16）：每供体
raw counts 聚合 → log1p CPT → 每基因组间供体 MW-U + BH + Cliff's
delta，与细胞级结果并列输出（donor_level 字段）——细胞级检验把
供体内细胞当独立样本，伪重复夸大显著性（59900 实测 15/15→0/15）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run
from scipy.stats import mannwhitneyu
from statsmodels.stats.multitest import multipletests


def _cat_cols(adata: Any) -> str:
    """列出可作分组的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def main() -> None:
    """主流程：两组定向 rank_genes_groups → DEG csv + 火山图 + 上下调 top。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    groupby = str(args.get("groupby", "")).strip()
    group_a = str(args.get("group_a", "")).strip()
    group_b = str(args.get("group_b", "")).strip()
    if not (groupby and group_a and group_b):
        raise ValueError(
            "groupby/group_a/group_b are all required, e.g. "
            "{'groupby': 'condition', 'group_a': 'treated', "
            "'group_b': 'control'}")
    method = str(args.get("method", "wilcoxon"))
    top_n = int(args.get("top_n", 20))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    if groupby not in adata.obs:
        raise ValueError(
            f"groupby column {groupby!r} not in obs; available: "
            f"{_cat_cols(adata)}")
    vals = set(adata.obs[groupby].astype(str))
    missing = [g for g in (group_a, group_b) if g not in vals]
    if missing:
        raise ValueError(
            f"group values {missing} not in obs[{groupby!r}]; "
            f"existing: {sorted(vals)[:20]}")
    if group_a == group_b:
        raise ValueError("group_a and group_b must differ")

    n_a = int((adata.obs[groupby].astype(str) == group_a).sum())
    n_b = int((adata.obs[groupby].astype(str) == group_b).sum())
    if n_a < 5 or n_b < 5:
        raise ValueError(
            f"too few cells: {group_a}={n_a}, {group_b}={n_b} (need >=5 each)")

    sc.tl.rank_genes_groups(adata, groupby=groupby, groups=[group_a],
                            reference=group_b, method=method, use_raw=True)
    result = adata.uns["rank_genes_groups"]
    df = pd.DataFrame({
        "gene": [str(g) for g in result["names"][group_a]],
        "score": np.asarray(result["scores"][group_a], dtype=float),
        "log2fc": np.asarray(result["logfoldchanges"][group_a], dtype=float),
        "pval": np.asarray(result["pvals"][group_a], dtype=float),
        "pval_adj": np.asarray(result["pvals_adj"][group_a], dtype=float),
    })
    df["direction"] = np.where(df["log2fc"] > 0, "up_in_" + group_a,
                               "down_in_" + group_a)

    ds_dir = WS_ROOT / args["dataset_id"] / "de"
    ds_dir.mkdir(parents=True, exist_ok=True)
    csv_path = ds_dir / f"de_{group_a}_vs_{group_b}.csv"
    df.to_csv(csv_path, index=False)

    # 火山图：log2fc × -log10(p_adj)（p_adj=0 截断 1e-12）
    sig = (df["pval_adj"] < 0.05) & (df["log2fc"].abs() >= 0.25)
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(df.loc[~sig, "log2fc"],
               -np.log10(np.maximum(df.loc[~sig, "pval_adj"], 1e-12)),
               s=4, c="#b8b8b8", linewidths=0, label="ns")
    up = sig & (df["log2fc"] > 0)
    dn = sig & (df["log2fc"] < 0)
    ax.scatter(df.loc[up, "log2fc"],
               -np.log10(np.maximum(df.loc[up, "pval_adj"], 1e-12)),
               s=6, c="#d84b3b", linewidths=0, label=f"up in {group_a}")
    ax.scatter(df.loc[dn, "log2fc"],
               -np.log10(np.maximum(df.loc[dn, "pval_adj"], 1e-12)),
               s=6, c="#3b7dd8", linewidths=0, label=f"down in {group_a}")
    ax.axhline(-np.log10(0.05), ls="--", lw=0.8, c="grey")
    ax.axvline(0.25, ls="--", lw=0.8, c="grey")
    ax.axvline(-0.25, ls="--", lw=0.8, c="grey")
    ax.set_xlabel("log2 fold change")
    ax.set_ylabel("-log10(adjusted p)")
    ax.set_title(f"{group_a} (n={n_a}) vs {group_b} (n={n_b})", fontsize=10)
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    volcano_png = ds_dir / f"volcano_{group_a}_vs_{group_b}.png"
    fig.savefig(volcano_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    def _top(sub: pd.DataFrame) -> list[dict[str, Any]]:
        return [
            {"gene": r["gene"], "log2fc": round(float(r["log2fc"]), 2),
             "pval_adj": float(f"{r['pval_adj']:.2e}")}
            for _, r in sub.head(top_n).iterrows()]

    up_df = df[(df["log2fc"] > 0) & (df["pval_adj"] < 0.05)].sort_values(
        "score", ascending=False)
    dn_df = df[(df["log2fc"] < 0) & (df["pval_adj"] < 0.05)].sort_values(
        "score")

    # 供体级 pseudobulk（donor_col 给定时，与细胞级并列）
    donor_level: dict[str, Any] | None = None
    donor_col = str(args.get("donor_col", "")).strip()
    if donor_col:
        if donor_col not in adata.obs:
            raise ValueError(
                f"donor_col {donor_col!r} not in obs; available: "
                f"{_cat_cols(adata)}")
        don_s = adata.obs[donor_col].astype(str)
        # 供体×组唯一性（混组供体无法归组）
        d_grp = adata.obs.groupby(don_s)[groupby].agg(
            lambda s: s.astype(str).unique().tolist())
        bad = d_grp[d_grp.map(len) > 1]
        if not bad.empty:
            raise ValueError(
                f"donors with mixed {groupby} values: {bad.index.tolist()[:10]}")
        d_grp = d_grp.map(lambda v: v[0])
        don_a = sorted(d_grp[d_grp == group_a].index)
        don_b = sorted(d_grp[d_grp == group_b].index)
        if len(don_a) < 3 or len(don_b) < 3:
            raise ValueError(
                f"need >=3 donors per group for pseudobulk: "
                f"{group_a}={len(don_a)}, {group_b}={len(don_b)}")

        # raw counts 供体聚合（onehot 向量矩阵乘，mat 恒为 细胞×基因）
        mat = adata.raw.X if adata.raw is not None else adata.X
        genes_d = (adata.raw.var_names if adata.raw is not None
                   else adata.var_names).astype(str)
        donors = don_a + don_b
        rows = []
        for d in donors:
            m = (don_s == d).to_numpy().astype(np.float64)
            rows.append(np.asarray(mat.T @ m).ravel())
        pb = np.vstack(rows)  # 供体 × 基因 counts
        cpt = np.log1p(pb / pb.sum(axis=1, keepdims=True) * 1e4)
        # 低表达过滤：非零供体 >=3
        keep = (pb > 0).sum(axis=0) >= 3
        cpt = cpt[:, keep]
        gene_k = np.asarray(genes_d)[keep]

        a_rows, b_rows = cpt[:len(don_a)], cpt[len(don_a):]
        drows = []
        for j, g in enumerate(gene_k):
            x1, x2 = a_rows[:, j], b_rows[:, j]
            u, p = mannwhitneyu(x1, x2, alternative="two-sided")
            drows.append({
                "gene": str(g),
                "mean_lfc_a": round(float(x1.mean()), 3),
                "mean_lfc_b": round(float(x2.mean()), 3),
                "log2fc": round(float(x1.mean() - x2.mean()), 3),
                "cliffs_delta": round(
                    float(2 * u / (len(don_a) * len(don_b)) - 1), 3),
                "p": float(p)})
        dres = pd.DataFrame(drows)
        dres["q"] = multipletests(dres["p"], method="fdr_bh")[1]
        dres["q"] = dres["q"].map(lambda x: float(f"{x:.2e}"))
        dres["p"] = dres["p"].map(lambda x: float(f"{x:.2e}"))
        dres["direction"] = np.where(dres["log2fc"] > 0,
                                     "up_in_" + group_a, "down_in_" + group_a)
        dres = dres.sort_values("p").reset_index(drop=True)
        donor_csv = ds_dir / f"de_donor_{group_a}_vs_{group_b}.csv"
        dres.to_csv(donor_csv, index=False)

        def _dtop(sub: pd.DataFrame) -> list[dict[str, Any]]:
            return [{"gene": r["gene"], "log2fc": r["log2fc"],
                     "q": r["q"]} for _, r in sub.head(top_n).iterrows()]

        dsig_up = dres[(dres["q"] < 0.05) & (dres["log2fc"] > 0)]
        dsig_dn = dres[(dres["q"] < 0.05) & (dres["log2fc"] < 0)]
        donor_level = {
            "donor_col": donor_col,
            "n_donors_a": len(don_a), "n_donors_b": len(don_b),
            "n_genes_tested": int(keep.sum()),
            "n_sig_up": int(len(dsig_up)), "n_sig_down": int(len(dsig_dn)),
            "up": _dtop(dsig_up), "down": _dtop(dsig_dn),
            "csv": str(donor_csv),
            "note": (f"pseudobulk：每供体 raw counts 聚合→log1p CPT→"
                     f"组间 MW-U({len(don_a)}v{len(don_b)})+BH——与细胞级"
                     "并列；小 n 供体检验功效有限（如 5v5 完全分离"
                     "极值 p≈7.9e-3），供体级不显著不等于无差异"),
        }

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "comparison": f"{group_a}_vs_{group_b}",
        "groupby": groupby,
        "n_cells_a": n_a, "n_cells_b": n_b,
        "n_sig_up": int(len(up_df)), "n_sig_down": int(len(dn_df)),
        "up": _top(up_df),
        "down": _top(dn_df),
        "csv": str(csv_path),
        "volcano_png": str(volcano_png),
        "donor_level": donor_level,
    })


if __name__ == "__main__":
    run(main)
