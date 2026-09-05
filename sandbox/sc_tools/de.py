"""sc_de：组间差异分析（Phase 33，对齐 toolsv1 server_differential_analysis）。

stdin: {"dataset_id": ..., "groupby": "condition",
        "group_a": "treated", "group_b": "control",
        "method": "wilcoxon", "top_n": 20}
需 processed.h5ad（sc_process 产物）。obs 需含 groupby 列
（源数据自带，如 condition/sample/批次的注释列）。
rank_genes_groups 定向对比 a vs b：上调 = log2fc > 0（a 相对 b）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata) -> str:
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

    def _top(sub: pd.DataFrame) -> list[dict]:
        return [
            {"gene": r["gene"], "log2fc": round(float(r["log2fc"]), 2),
             "pval_adj": float(f"{r['pval_adj']:.2e}")}
            for _, r in sub.head(top_n).iterrows()]

    up_df = df[(df["log2fc"] > 0) & (df["pval_adj"] < 0.05)].sort_values(
        "score", ascending=False)
    dn_df = df[(df["log2fc"] < 0) & (df["pval_adj"] < 0.05)].sort_values(
        "score")

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
    })


if __name__ == "__main__":
    run(main)
