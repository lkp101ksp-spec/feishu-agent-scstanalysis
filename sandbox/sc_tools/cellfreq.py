"""sc_cellfreq：细胞组成比较（Phase 33，对齐 toolsv1 server_cell_freq_merged）。

stdin: {"dataset_id": ..., "by": "sample", "group": "condition",
        "celltype_col": "leiden", "donor_col": ""}
需 processed.h5ad。按 by 列（样本/受试者）统计各簇细胞比例 →
比例表 csv + 堆叠柱状图 png；group 列给出时每簇做卡方检验
（该簇 vs 其余 × 各 group，按样本合并计数；返回原始 chi2/p，未做多重校正）。
group 值域恰 2 时附加（2026-09-13 命运偏向增强）：
每簇 Fisher 精确检验（odds_ratio + BH fisher_q）+ Ro/e 组织分布
偏好指数（observed/expected，>1 偏好）+ Ro/e 热图 + fate_bias
摘要；celltype_col 传 palantir_branch 即分支命运偏向分析。
group >2 值时仅卡方 + note 降级。
donor_col 给定时附加供体级组成检验（2026-09-16）：每供体簇占比 →
组间供体 MW-U + BH + Cliff's delta（donor_level 字段并列输出）——
细胞级卡方把供体内细胞当独立样本，伪重复夸大显著性。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run
from scipy.stats import chi2_contingency, fisher_exact, mannwhitneyu
from statsmodels.stats.multitest import multipletests


def _cat_cols(adata: Any) -> str:
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
    tests: list[dict[str, Any]] = []
    roe_df: pd.DataFrame | None = None
    roe_png: Any = None
    group_note: str | None = None
    if group:
        g_s = adata.obs[group].astype(str)
        g_levels = sorted(g_s.unique())
        two_g = len(g_levels) == 2
        roe_rows: dict[str, dict[str, float]] = {}
        for c in order:
            in_c = (ct_s == c)
            table = pd.crosstab(g_s, in_c).reindex(columns=[True, False],
                                                   fill_value=0)
            if table.shape[0] < 2:
                continue
            obs = table.to_numpy()
            chi2, p, _, exp = chi2_contingency(obs)
            entry: dict[str, Any] = {
                "cluster": c,
                "n_cells": int(in_c.sum()),
                "overall_pct": round(float(overall_pct[c]), 4),
                "chi2": round(float(chi2), 2),
                "p": float(f"{p:.3e}"),
            }
            if two_g:
                # 命运偏向增强：Fisher 精确检验 + Ro/e（observed/expected）
                oratio, fp = fisher_exact(obs)
                entry["odds_ratio"] = (round(float(oratio), 3)
                                       if np.isfinite(oratio) else None)
                entry["fisher_p"] = float(f"{fp:.3e}")
                roe_rows[c] = {g: float(obs[i, 0] / exp[i, 0])
                               if exp[i, 0] > 0 else float("nan")
                               for i, g in enumerate(g_levels)}
            tests.append(entry)
        tests.sort(key=lambda t: t["p"])
        if two_g and roe_rows:
            # Fisher p 做 BH 多重校正；Ro/e 矩阵落列 + 热图
            qvals = multipletests([t["fisher_p"] for t in tests],
                                  method="fdr_bh")[1]
            g1, g2 = g_levels
            for t, q in zip(tests, qvals):
                t["fisher_q"] = float(f"{q:.3e}")
                r = roe_rows[t["cluster"]]
                t[f"roe_{g1}"], t[f"roe_{g2}"] = (
                    round(r[g1], 3), round(r[g2], 3))
            roe_df = pd.DataFrame(roe_rows).T[g_levels]
            roe_csv = ds_dir / f"roe_by_{group}.csv"
            roe_df.to_csv(roe_csv)
            lg = np.log2(roe_df.to_numpy())
            v = float(np.nanmax(np.abs(lg))) if np.isfinite(lg).any() else 1.0
            fig, ax = plt.subplots(figsize=(3.2, max(2.5, 0.45 * len(roe_df))),
                                   dpi=150)
            im = ax.imshow(lg, aspect="auto", cmap="RdBu_r",
                           vmin=-v, vmax=v)
            ax.set_xticks(range(2))
            ax.set_xticklabels(g_levels, fontsize=9)
            ax.set_yticks(range(len(roe_df)))
            ax.set_yticklabels(roe_df.index, fontsize=8)
            for i in range(lg.shape[0]):
                for j in range(2):
                    ax.text(j, i, f"{roe_df.iloc[i, j]:.2f}",
                            ha="center", va="center", fontsize=7)
            fig.colorbar(im, ax=ax, label="log2(Ro/e)", shrink=0.8)
            ax.set_title(f"Tissue preference Ro/e by {group}", fontsize=9)
            fig.tight_layout()
            roe_png = ds_dir / "cellfreq_roe.png"
            fig.savefig(roe_png, dpi=150, bbox_inches="tight")
            plt.close(fig)
        elif group and not two_g:
            group_note = (f"group has {len(g_levels)} levels; "
                          "chi-square only (Fisher/Ro-e need exactly 2)")

    # 命运偏向摘要：Fisher 显著簇按 q 升序 top5（Ro/e>1 的组为 higher_in）
    fate_bias: list[dict[str, Any]] = []
    if roe_df is not None:
        g_levels2 = list(roe_df.columns)
        sig = [t for t in tests if t.get("fisher_q", 1.0) < 0.05]
        sig.sort(key=lambda t: t["fisher_q"])
        for t in sig[:5]:
            r1 = t[f"roe_{g_levels2[0]}"]
            fate_bias.append({
                "cluster": t["cluster"],
                "odds_ratio": t["odds_ratio"],
                "fisher_q": t["fisher_q"],
                "higher_in": g_levels2[0] if r1 > 1 else g_levels2[1]})

    # 供体级组成检验（donor_col 给定时，与细胞级并列）
    donor_level: dict[str, Any] | None = None
    donor_col = str(args.get("donor_col", "")).strip()
    if donor_col:
        if not group:
            raise ValueError("donor_col requires group column")
        if donor_col not in adata.obs:
            raise ValueError(
                f"donor_col {donor_col!r} not in obs; available: "
                f"{_cat_cols(adata)}")
        don_s = adata.obs[donor_col].astype(str)
        g_s = adata.obs[group].astype(str)
        d_grp = pd.Series({
            d: gss.unique().tolist() for d, gss in g_s.groupby(don_s)})
        bad = d_grp[d_grp.map(len) > 1]
        if not bad.empty:
            raise ValueError(
                f"donors with mixed {group} values: {bad.index.tolist()[:10]}")
        d_grp = d_grp.map(lambda v: v[0])
        g_levels_d = sorted(set(d_grp))
        if len(g_levels_d) != 2:
            raise ValueError(
                f"donor-level test needs exactly 2 groups in {group}, "
                f"got {g_levels_d}")
        don_counts = pd.crosstab(don_s, ct_s).reindex(
            columns=order, fill_value=0)
        don_props = don_counts.div(don_counts.sum(axis=1), axis=0)
        ga, gb = g_levels_d[0], g_levels_d[1]
        don_a = sorted(d_grp[d_grp == ga].index)
        don_b = sorted(d_grp[d_grp == gb].index)
        if len(don_a) < 3 or len(don_b) < 3:
            raise ValueError(
                f"need >=3 donors per group: {ga}={len(don_a)}, "
                f"{gb}={len(don_b)}")
        drows = []
        for c in order:
            x1 = don_props.loc[don_a, c].to_numpy(dtype=float)
            x2 = don_props.loc[don_b, c].to_numpy(dtype=float)
            u, p = mannwhitneyu(x1, x2, alternative="two-sided")
            drows.append({
                "cluster": c,
                "mean_prop_a": round(float(x1.mean()), 4),
                "mean_prop_b": round(float(x2.mean()), 4),
                "delta_a-b": round(float(x1.mean() - x2.mean()), 4),
                "cliffs_delta": round(
                    float(2 * u / (len(don_a) * len(don_b)) - 1), 3),
                "p": float(f"{p:.3e}")})
        dres = pd.DataFrame(drows)
        dres.insert(0, "group_a", ga)
        dres.insert(1, "group_b", gb)
        dres["q"] = multipletests(dres["p"], method="fdr_bh")[1]
        dres["q"] = dres["q"].map(lambda x: float(f"{x:.3e}"))
        dres = dres.sort_values("p").reset_index(drop=True)
        props_out = don_props.copy()
        props_out.insert(0, group, d_grp.reindex(props_out.index).to_numpy())
        donor_csv = ds_dir / f"donor_props_by_{donor_col}.csv"
        props_out.to_csv(donor_csv)
        deets_csv = ds_dir / f"donor_test_by_{donor_col}.csv"
        dres.to_csv(deets_csv, index=False)
        donor_level = {
            "donor_col": donor_col,
            "n_donors_a": len(don_a), "n_donors_b": len(don_b),
            "group_a": ga, "group_b": gb,
            "n_sig": int((dres["q"] < 0.05).sum()),
            "top": [{"cluster": r["cluster"],
                     "delta_a-b": r["delta_a-b"],
                     "cliffs_delta": r["cliffs_delta"], "q": r["q"]}
                    for _, r in dres.head(5).iterrows()],
            "props_csv": str(donor_csv),
            "csv": str(deets_csv),
            "note": (f"供体级：每{donor_col}簇占比→MW-U"
                     f"({len(don_a)}v{len(don_b)})+BH——与细胞级卡方并列；"
                     "小 n 功效有限（5v5 完全分离极值 p≈7.9e-3），"
                     "不显著≠无差异"),
        }

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
        "fate_bias": fate_bias or None,
        "donor_level": donor_level,
        "roe_csv": str(ds_dir / f"roe_by_{group}.csv"
                       ) if roe_df is not None else None,
        "roe_png": str(roe_png) if roe_png else None,
        "group_note": group_note,
        "csv": str(csv_path),
        "bar_png": str(bar_png),
    })


if __name__ == "__main__":
    run(main)
