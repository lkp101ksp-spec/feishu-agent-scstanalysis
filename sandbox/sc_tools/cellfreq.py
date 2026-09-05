"""sc_cellfreq：细胞组成比较（Phase 33，对齐 toolsv1 server_cell_freq_merged）。

stdin: {"dataset_id": ..., "by": "sample", "group": "condition",
        "celltype_col": "leiden"}
需 processed.h5ad。按 by 列（样本/受试者）统计各簇细胞比例 →
比例表 csv + 堆叠柱状图 png；group 列给出时每簇做卡方检验
（该簇 vs 其余 × 各 group，按样本合并计数；返回原始 chi2/p，未做多重校正）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run
from scipy.stats import chi2_contingency


def _cat_cols(adata) -> str:
    """列出可作分组的 obs 列（2..50 个取值），错误消息引导自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def main() -> None:
    """主流程：比例表 + 堆叠柱状图 +（可选）每簇卡方检验。"""
    import matplotlib.pyplot as plt

    args = read_args()
    by = str(args.get("by", "")).strip()
    group = str(args.get("group", "")).strip()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    if not by:
        raise ValueError("by column is required, e.g. 'sample' or 'subject'")

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    for col in [by, group, celltype_col]:
        if col and col not in adata.obs:
            raise ValueError(
                f"column {col!r} not in obs; available: {_cat_cols(adata)}")

    by_s = adata.obs[by].astype(str)
    ct_s = adata.obs[celltype_col].astype(str)
    order = sorted(ct_s.unique(), key=lambda c: (len(c), c))
    by_order = sorted(by_s.unique())

    # 样本 × 簇 计数与比例
    counts = (pd.crosstab(by_s, ct_s).reindex(columns=order, fill_value=0)
              .loc[by_order])
    props = counts.div(counts.sum(axis=1), axis=0)
    overall = counts.sum(axis=0)
    overall_pct = overall / overall.sum()

    ds_dir = WS_ROOT / args["dataset_id"] / "cellfreq"
    ds_dir.mkdir(parents=True, exist_ok=True)
    csv_path = ds_dir / f"proportion_by_{by}.csv"
    out = props.copy()
    out.insert(0, "n_cells", counts.sum(axis=1))
    out.to_csv(csv_path)

    # 堆叠柱状图（每 by 水平一柱）
    fig, ax = plt.subplots(figsize=(max(6, 0.9 * len(by_order) + 2), 5),
                           dpi=150)
    bottom = np.zeros(len(by_order))
    cmap = plt.get_cmap("tab20")
    for i, c in enumerate(order):
        vals = props[c].to_numpy(dtype=float)
        ax.bar(range(len(by_order)), vals, bottom=bottom,
               color=cmap(i % 20), width=0.7, label=c)
        bottom += vals
    ax.set_xticks(range(len(by_order)))
    ax.set_xticklabels(by_order, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("cell proportion")
    ax.set_title(f"Cell composition by {by} (colored by {celltype_col})",
                 fontsize=10)
    ax.legend(fontsize=7, ncol=min(4, len(order)), loc="upper left",
              bbox_to_anchor=(1.01, 1))
    fig.tight_layout()
    bar_png = ds_dir / f"composition_by_{by}.png"
    fig.savefig(bar_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 每簇卡方检验（group 列给出时：该簇 vs 其余 × group）
    tests: list[dict] = []
    if group:
        g_s = adata.obs[group].astype(str)
        for c in order:
            in_c = (ct_s == c)
            table = pd.crosstab(g_s, in_c).reindex(columns=[True, False],
                                                   fill_value=0)
            if table.shape[0] < 2:
                continue
            chi2, p, _, _ = chi2_contingency(table.to_numpy())
            tests.append({
                "cluster": c,
                "n_cells": int(in_c.sum()),
                "overall_pct": round(float(overall_pct[c]), 4),
                "chi2": round(float(chi2), 2),
                "p": float(f"{p:.3e}"),
            })
        tests.sort(key=lambda t: t["p"])

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "by": by,
        "celltype_col": celltype_col,
        "n_levels": len(by_order),
        "overall": {c: {"n_cells": int(overall[c]),
                        "pct": round(float(overall_pct[c]), 4)}
                    for c in order},
        "chi2_tests": tests,
        "chi2_note": ("per-cluster chi-square (this cluster vs rest x "
                      "group); raw p, no multiple-testing correction"
                      ) if tests else None,
        "csv": str(csv_path),
        "bar_png": str(bar_png),
    })


if __name__ == "__main__":
    run(main)
