"""sc_cellchat：细胞通讯分析（Phase 34，对齐 toolsv1 server_cellchat 单组推断）。

stdin: {"dataset_id": ..., "celltype_col": "leiden", "species": "human",
        "expr_prop": 0.1, "min_cells": 10, "top_n": 30,
        "max_cells_per_group": 0}
需 processed.h5ad（sc_process 产物，含 raw）。
liana 内置 cellchat 方法 + 随包资源库（human=consensus / mouse=mouseconsensus），
容器断网可用。多组比较（toolsv1 part1-5 套件）本版不做。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


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


def main() -> None:
    """主流程：liana cellchat → LR 全量 csv + top LR dotplot + 互作计数热图。"""
    import liana as li
    import matplotlib.pyplot as plt

    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    species = str(args.get("species", "human")).strip().lower()
    expr_prop = float(args.get("expr_prop", 0.1))
    min_cells = int(args.get("min_cells", 10))
    top_n = int(args.get("top_n", 30))
    max_cells_per_group = int(args.get("max_cells_per_group", 0))
    resource = {"human": "consensus", "mouse": "mouseconsensus"}.get(species)
    if resource is None:
        raise ValueError(f"species must be human or mouse, got {species!r}")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        raise ValueError(
            f"celltype column {celltype_col!r} not in obs; available: "
            f"{_cat_cols(adata)}")
    labels = adata.obs[celltype_col].astype(str)
    dropped = labels.value_counts().loc[lambda s: s < min_cells].index.tolist()
    if dropped:
        adata = adata[~labels.isin(dropped)].copy()
        labels = adata.obs[celltype_col].astype(str)
    if labels.nunique() < 2:
        raise ValueError(
            f"need >=2 cell types with >= {min_cells} cells each; "
            f"dropped too-small: {dropped}")

    # 分层抽样（可选）：每组取 min(max_cells_per_group, 组大小)，
    # 固定 random_state=42 保证可复现；0=全量。
    n_cells_total = adata.n_obs
    if max_cells_per_group > 0:
        idx: list[Any] = []
        for _, g in adata.obs.groupby(celltype_col):
            idx.extend(g.sample(n=min(max_cells_per_group, len(g)),
                                random_state=42).index)
        adata = adata[idx].copy()
        labels = adata.obs[celltype_col].astype(str)
    subsampled = adata.n_obs < n_cells_total

    li.mt.cellchat(adata, groupby=celltype_col, resource_name=resource,
                   expr_prop=expr_prop, use_raw=True, verbose=False)
    lr = adata.uns["liana_res"].copy()
    score_col = _find_col(lr, "lr_probs", "probs")
    pval_col = _find_col(lr, "cellchat_pvals", "pvals")
    lr = lr.rename(columns={score_col: "lr_score", pval_col: "pval"})
    lr["pair"] = lr["source"].astype(str) + "->" + lr["target"].astype(str)
    lr["lr"] = (lr["ligand_complex"].astype(str) + "|"
                + lr["receptor_complex"].astype(str))
    lr = lr.sort_values(["pval", "lr_score"],
                        ascending=[True, False]).reset_index(drop=True)

    out_dir = WS_ROOT / args["dataset_id"] / "cellchat"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "cellchat_lr.csv"
    lr.to_csv(csv_path, index=False)

    # dotplot：top LR 对（行）× top 细胞类型对（列），大小=-log10(p)，色=lr_score
    sig = lr[lr["pval"] < 0.05]
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
                       s=20 + 120 * -np.log10(max(r["pval"], 1e-12)) / 12,
                       c=[r["lr_score"]], cmap="viridis", vmin=0,
                       vmax=float(lr["lr_score"].max()) or 1.0,
                       linewidths=0)
    ax.set_xticks(range(len(top_pairs)))
    ax.set_xticklabels(top_pairs, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(top_lrs)))
    ax.set_yticklabels(top_lrs, fontsize=7)
    ax.set_title(f"top LR interactions ({species}, {resource})", fontsize=10)
    fig.tight_layout()
    dot_png = out_dir / "cellchat_dotplot.png"
    fig.savefig(dot_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 互作计数热图：source × target 显著互作数
    types = sorted(labels.unique().tolist())
    mat = pd.DataFrame(0, index=types, columns=types)
    for _, r in sig.iterrows():
        mat.loc[str(r["source"]), str(r["target"])] += 1
    fig, ax = plt.subplots(figsize=(max(4, 0.7 * len(types) + 2),
                                    max(3, 0.6 * len(types) + 2)))
    im = ax.imshow(mat.to_numpy(), cmap="Reds")
    ax.set_xticks(range(len(types)), types, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(types)), types, fontsize=8)
    ax.set_xlabel("target")
    ax.set_ylabel("source")
    ax.set_title("significant interaction counts (p<0.05)", fontsize=10)
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
         "pval": float(f"{r['pval']:.2e}")}
        for _, r in lr.head(top_n).iterrows()]

    payload = {
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "celltype_col": celltype_col,
        "resource": resource,
        "n_celltypes": int(labels.nunique()),
        "dropped_small_types": [str(d) for d in dropped],
        "n_cells_used": int(adata.n_obs),
        "subsampled": subsampled,
        "n_pairs_tested": int(len(lr)),
        "n_sig": int(len(sig)),
        "top": top,
        "csv": str(csv_path),
        "dotplot_png": str(dot_png),
        "heatmap_png": str(heat_png),
    }
    if subsampled:
        payload["note"] = (
            f"按 {celltype_col} 分层抽样：每组最多 {max_cells_per_group} "
            f"细胞（{n_cells_total}→{adata.n_obs}），结果为抽样估计")
    emit(payload)


if __name__ == "__main__":
    run(main)
