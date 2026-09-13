"""sc_cellchat：细胞通讯分析（Phase 34 单组 / Phase 47 聚合+差异通讯）。

stdin: {"dataset_id": ..., "celltype_col": "leiden", "species": "human",
        "expr_prop": 0.1, "min_cells": 10, "top_n": 30,
        "max_cells_per_group": 0,
        "method": "cellchat",      # Phase 47：cellchat | rank_aggregate
        "group_col": ""}           # Phase 47：恰两取值分组列 → 两组差异
需 processed.h5ad（sc_process 产物，含 raw）。
liana 内置资源库（human=consensus / mouse=mouseconsensus），容器断网可用。

容器探针（2026-09-13，liana 1.10.0 断网实测）：
- rank_aggregate(adata, groupby, resource_name, expr_prop, n_perms,
  use_raw, verbose) → uns['liana_res'] 列 source/target/ligand_complex/
  receptor_complex/lr_means/cellphone_pvals/expr_prod/scaled_weight/
  lr_logfc/spec_weight/lrscore/specificity_rank/magnitude_rank
- 统一显著口径 sig_metric（越小越显著）：cellchat 路=cellchat_pvals、
  rank_aggregate 路=magnitude_rank；阈值均 0.05（liana 教程口径）
- assert_covered 要求资源 L/R 基因在 var_names 缺失比例 ≤0.98
  （合成冒烟数据必须并入资源基因低表达背景，否则 ValueError）
- 合成 240 细胞 5.8s（n_perms=10）；19149×14 类 rank_aggregate
  全默认（n_perms=1000）实测 165s/4.2GB，52117 行 sig 3111
  （远低于 timeout 3600s 与内存 8GB，无需上调）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run

METHODS = ("cellchat", "rank_aggregate")
SIG_THRESH = 0.05


def _cat_cols(adata: Any) -> str:
    """列出可作细胞标签的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _find_col(df: pd.DataFrame, preferred: str, suffix: str) -> str:
    """防御式列解析：liana 版本间列名可能漂移，按首选名→后缀兜底。"""
    if preferred in df.columns:
        return preferred
    for c in df.columns:
        if str(c).endswith(suffix):
            return str(c)
    raise KeyError(
        f"cannot locate score/pval column ({preferred!r} or *{suffix}); "
        f"liana columns: {list(df.columns)}")


def _prepare(adata: Any, celltype_col: str, min_cells: int,
             max_cells_per_group: int) -> tuple[Any, Any, list[str], bool, int]:
    """小群剔除 + 可选分层抽样 → (adata, labels, dropped, subsampled, n_total)。"""
    labels = adata.obs[celltype_col].astype(str)
    dropped = labels.value_counts().loc[lambda s: s < min_cells].index.tolist()
    if dropped:
        adata = adata[~labels.isin(dropped)].copy()
        labels = adata.obs[celltype_col].astype(str)
    if labels.nunique() < 2:
        raise ValueError(
            f"need >=2 cell types with >= {min_cells} cells each; "
            f"dropped too-small: {dropped}")
    n_cells_total = adata.n_obs
    if max_cells_per_group > 0:
        idx: list[Any] = []
        for _, g in adata.obs.groupby(celltype_col):
            idx.extend(g.sample(n=min(max_cells_per_group, len(g)),
                                random_state=42).index)
        adata = adata[idx].copy()
        labels = adata.obs[celltype_col].astype(str)
    return adata, labels, dropped, adata.n_obs < n_cells_total, n_cells_total


def _count_matrix(sig: pd.DataFrame, types: list[str]) -> pd.DataFrame:
    """source × target 显著互作计数矩阵。"""
    mat = pd.DataFrame(0, index=types, columns=types)
    for _, r in sig.iterrows():
        mat.loc[str(r["source"]), str(r["target"])] += 1
    return mat


def _infer_one(adata: Any, labels: Any, celltype_col: str, resource: str,
               method: str, expr_prop: float, top_n: int,
               out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """单组推断（统一口径：lr_score 越大越强、sig_metric 越小越显著）。

    → lr 全量 csv + top LR dotplot + 互作计数热图；
    返回 (lr 全表, sig 显著表, 单组产物 payload 段)。
    """
    import liana as li
    import matplotlib.pyplot as plt

    if method == "cellchat":
        li.mt.cellchat(adata, groupby=celltype_col, resource_name=resource,
                       expr_prop=expr_prop, use_raw=True, verbose=False)
        lr = adata.uns["liana_res"].copy()
        score_col = _find_col(lr, "lr_probs", "probs")
        pval_col = _find_col(lr, "cellchat_pvals", "pvals")
        lr = lr.rename(columns={score_col: "lr_score",
                                pval_col: "sig_metric"})
    else:
        li.mt.rank_aggregate(adata, groupby=celltype_col,
                             resource_name=resource, expr_prop=expr_prop,
                             use_raw=True, verbose=False)
        lr = adata.uns["liana_res"].copy()
        lr = lr.rename(columns={"lr_means": "lr_score",
                                "magnitude_rank": "sig_metric"})
    lr["pair"] = lr["source"].astype(str) + "->" + lr["target"].astype(str)
    lr["lr"] = (lr["ligand_complex"].astype(str) + "|"
                + lr["receptor_complex"].astype(str))
    lr = lr.sort_values(["sig_metric", "lr_score"],
                        ascending=[True, False]).reset_index(drop=True)
    sig = lr[lr["sig_metric"] < SIG_THRESH]

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "cellchat_lr.csv"
    lr.to_csv(csv_path, index=False)

    # dotplot：top LR 对（行）× top 细胞类型对（列），
    # 大小=-log10(sig_metric)，色=lr_score
    top_lrs = lr["lr"].drop_duplicates().head(top_n).tolist()
    top_pairs = (sig["pair"].value_counts().head(12).index.tolist()
                 or lr["pair"].drop_duplicates().head(12).tolist())
    sub = lr[lr["lr"].isin(top_lrs) & lr["pair"].isin(top_pairs)]
    fig, ax = plt.subplots(figsize=(max(4, 0.9 * len(top_pairs) + 2),
                                    max(4, 0.32 * len(top_lrs) + 2)))
    for yi, lr_name in enumerate(top_lrs):
        for xi, pair in enumerate(top_pairs):
            hit = sub[(sub["lr"] == lr_name) & (sub["pair"] == pair)]
            if hit.empty:
                continue
            r = hit.iloc[0]
            ax.scatter(xi, yi,
                       s=20 + 120 * -np.log10(max(r["sig_metric"], 1e-12)) / 12,
                       c=[r["lr_score"]], cmap="viridis", vmin=0,
                       vmax=float(lr["lr_score"].max()) or 1.0,
                       linewidths=0)
    ax.set_xticks(range(len(top_pairs)))
    ax.set_xticklabels(top_pairs, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(top_lrs)))
    ax.set_yticklabels(top_lrs, fontsize=7)
    ax.set_title(f"top LR interactions ({method})", fontsize=10)
    fig.tight_layout()
    dot_png = out_dir / "cellchat_dotplot.png"
    fig.savefig(dot_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 互作计数热图：source × target 显著互作数
    types = sorted(labels.unique().tolist())
    mat = _count_matrix(sig, types)
    fig, ax = plt.subplots(figsize=(max(4, 0.7 * len(types) + 2),
                                    max(3, 0.6 * len(types) + 2)))
    im = ax.imshow(mat.to_numpy(), cmap="Reds")
    ax.set_xticks(range(len(types)), types, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(types)), types, fontsize=8)
    ax.set_xlabel("target")
    ax.set_ylabel("source")
    ax.set_title(f"significant interaction counts ({method})", fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    heat_png = out_dir / "cellchat_heatmap.png"
    fig.savefig(heat_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top = [
        {"ligand": str(r["ligand_complex"]),
         "receptor": str(r["receptor_complex"]),
         "source": str(r["source"]), "target": str(r["target"]),
         "lr_score": round(float(r["lr_score"]), 3),
         "sig_metric": float(f"{r['sig_metric']:.2e}")}
        for _, r in lr.head(top_n).iterrows()]
    payload = {"n_pairs_tested": int(len(lr)), "n_sig": int(len(sig)),
               "top": top, "csv": str(csv_path),
               "dotplot_png": str(dot_png), "heatmap_png": str(heat_png)}
    return lr, sig, payload


def _diff(lr1: pd.DataFrame, lr2: pd.DataFrame, g1: str, g2: str,
          types: list[str], out_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """两组差分：outer merge（LR×pair 全组合）→ delta/up_in → csv+红蓝热图。"""
    import matplotlib.pyplot as plt

    keep = ["lr", "pair", "ligand_complex", "receptor_complex",
            "source", "target", "lr_score", "sig_metric"]
    m = lr1[keep].merge(lr2[keep], on=["lr", "pair"], how="outer",
                        suffixes=("_g1", "_g2"))
    m["lr_score_g1"] = m["lr_score_g1"].fillna(0.0)
    m["lr_score_g2"] = m["lr_score_g2"].fillna(0.0)
    # merge 后缀吃掉 source/target（→_g1/_g2），回填供 _count_matrix 与 csv
    m["source"] = m["source_g1"].fillna(m["source_g2"])
    m["target"] = m["target_g1"].fillna(m["target_g2"])
    # NaN sig_metric（该组无此组合）经比较天然得 False
    m["sig_g1"] = m["sig_metric_g1"] < SIG_THRESH
    m["sig_g2"] = m["sig_metric_g2"] < SIG_THRESH
    m["delta_score"] = m["lr_score_g1"] - m["lr_score_g2"]
    m["up_in"] = np.where(m["delta_score"] > 0, g1, g2)
    m = m.sort_values("delta_score", key=abs,
                      ascending=False).reset_index(drop=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    diff_csv = out_dir / "diff_lr.csv"
    m.to_csv(diff_csv, index=False)

    mat1 = _count_matrix(m[m["sig_g1"]], types)
    mat2 = _count_matrix(m[m["sig_g2"]], types)
    dmat = (mat1 - mat2).to_numpy(dtype=float)
    vmax = float(np.abs(dmat).max()) or 1.0
    fig, ax = plt.subplots(figsize=(max(4, 0.7 * len(types) + 2),
                                    max(3, 0.6 * len(types) + 2)))
    im = ax.imshow(dmat, cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(types)), types, rotation=45, ha="right",
                  fontsize=8)
    ax.set_yticks(range(len(types)), types, fontsize=8)
    ax.set_xlabel("target")
    ax.set_ylabel("source")
    ax.set_title(f"sig interaction count diff ({g1} - {g2})", fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    heat_png = out_dir / "diff_heatmap.png"
    fig.savefig(heat_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return m, {"diff_csv": str(diff_csv), "diff_heatmap_png": str(heat_png)}


def main() -> None:
    """主流程：单组（现状）或两组差异通讯（group_col 恰两取值）。"""
    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    species = str(args.get("species", "human")).strip().lower()
    expr_prop = float(args.get("expr_prop", 0.1))
    min_cells = int(args.get("min_cells", 10))
    top_n = int(args.get("top_n", 30))
    max_cells_per_group = int(args.get("max_cells_per_group", 0))
    method = str(args.get("method", "cellchat")).strip().lower()
    group_col = str(args.get("group_col", "")).strip()
    if method not in METHODS:
        fail("INVALID_INPUT", f"method must be {METHODS}, got {method!r}")
        return
    resource = {"human": "consensus", "mouse": "mouseconsensus"}.get(species)
    if resource is None:
        fail("INVALID_INPUT",
             f"species must be human or mouse, got {species!r}")
        return

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        fail("INVALID_INPUT",
             f"celltype column {celltype_col!r} not in obs; available: "
             f"{_cat_cols(adata)}")
        return

    ds_dir = WS_ROOT / args["dataset_id"]
    if not group_col:
        # 单组（Phase 34 现状行为）
        adata, labels, dropped, subsampled, n_total = _prepare(
            adata, celltype_col, min_cells, max_cells_per_group)
        lr, sig, payload = _infer_one(
            adata, labels, celltype_col, resource, method, expr_prop,
            top_n, ds_dir / "cellchat")
        payload.update({
            "ok": True, "dataset_ref": args["dataset_id"], "method": method,
            "celltype_col": celltype_col, "resource": resource,
            "n_celltypes": int(labels.nunique()),
            "dropped_small_types": [str(d) for d in dropped],
            "n_cells_used": int(adata.n_obs), "subsampled": subsampled})
        if subsampled:
            payload["note"] = (
                f"按 {celltype_col} 分层抽样：每组最多 "
                f"{max_cells_per_group} 细胞（{n_total}→{adata.n_obs}），"
                "结果为抽样估计")
        emit(payload)
        return

    # 两组差异通讯（Phase 47）
    if group_col not in adata.obs:
        fail("INVALID_INPUT",
             f"group column {group_col!r} not in obs; available: "
             f"{_cat_cols(adata)}")
        return
    groups = sorted(adata.obs[group_col].astype(str).unique().tolist())
    if len(groups) != 2:
        fail("INVALID_INPUT",
             f"group_col {group_col!r} must have exactly 2 values for diff "
             f"mode, got {len(groups)}: {groups}; subset the data first "
             f"(multi-group pairwise not supported)")
        return
    g1, g2 = groups
    per_group: dict[str, Any] = {}
    lr_by: dict[str, pd.DataFrame] = {}
    sig_by: dict[str, pd.DataFrame] = {}
    types: set[str] = set()
    for g in (g1, g2):
        sub = adata[adata.obs[group_col].astype(str) == g].copy()
        sub, labels, dropped, subsampled, n_total = _prepare(
            sub, celltype_col, min_cells, max_cells_per_group)
        lr, sig, payload = _infer_one(
            sub, labels, celltype_col, resource, method, expr_prop,
            top_n, ds_dir / "cellchat" / g)
        lr_by[g], sig_by[g] = lr, sig
        types.update(labels.unique().tolist())
        per_group[g] = {
            "n_cells": int(sub.n_obs),
            "dropped_small_types": [str(d) for d in dropped],
            "subsampled": subsampled,
            "n_pairs_tested": payload["n_pairs_tested"],
            "n_sig": payload["n_sig"],
            "csv": payload["csv"], "dotplot_png": payload["dotplot_png"],
            "heatmap_png": payload["heatmap_png"]}
    m, diff_payload = _diff(lr_by[g1], lr_by[g2], g1, g2,
                            sorted(types), ds_dir / "cellchat_diff")
    top_delta = None
    if len(m):
        r = m.iloc[0]
        top_delta = {"lr": str(r["lr"]), "pair": str(r["pair"]),
                     "delta_score": round(float(r["delta_score"]), 3),
                     "up_in": str(r["up_in"])}
    emit({
        "ok": True, "mode": "diff", "dataset_ref": args["dataset_id"],
        "method": method, "group_col": group_col, "groups": [g1, g2],
        "celltype_col": celltype_col, "resource": resource,
        "n_sig_g1": int(len(sig_by[g1])), "n_sig_g2": int(len(sig_by[g2])),
        "top_delta": top_delta,
        "diff_csv": diff_payload["diff_csv"],
        "diff_heatmap_png": diff_payload["diff_heatmap_png"],
        "per_group": per_group,
    })


if __name__ == "__main__":
    run(main)
