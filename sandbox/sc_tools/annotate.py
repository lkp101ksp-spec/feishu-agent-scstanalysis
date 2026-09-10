"""sc_annotate：细胞类型注释（Phase 35，对齐 toolsv1 server_cell_annotation）。

stdin: {"dataset_id": ..., "method": "celltypist"|"markers",
        "model": "Immune_All_Low.pkl",              # celltypist 路
        "marker_sets": {"T cell": ["CD3D", ...]},   # markers 路
        "celltype_col": "leiden",                   # 簇标签列（投票/平滑）
        "out_col": "annotation"}                    # markers 路写回列名
需 processed.h5ad。两路结果均原地写回 processed.h5ad：
- celltypist 路：obs 增 celltypist_label/celltypist_conf。adata.raw 为
  log-norm 全基因，正好是 celltypist 期望输入；majority voting 以
  celltype_col 为 over_clustering。模型文件在 /opt/celltypist_models/
  （构建期预取，断网可用），model 不存在时错误列出现有模型。
- markers 路：逐集 score_genes(use_raw=True) → 簇均值 argmax →
  obs[out_col]；得分矩阵落 csv 供审阅。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run

MODEL_DIR = Path("/opt/celltypist_models")


def _cat_cols(adata: Any) -> str:
    """列出可作分组的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _umap_by_label(adata: Any, col: str, png_path: Path, title: str) -> None:
    """按标签列手绘 UMAP（避免 scanpy 多类图例截断）。"""
    import matplotlib.pyplot as plt

    labels = adata.obs[col].astype(str)
    cats = sorted(labels.unique().tolist())
    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(7, 5.5))
    umap = adata.obsm["X_umap"]
    for i, c in enumerate(cats):
        m = (labels == c).to_numpy()
        ax.scatter(umap[m, 0], umap[m, 1], s=5, color=cmap(i % 20),
                   label=f"{c} ({int(m.sum())})", linewidths=0)
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.0, 0.5),
              markerscale=2)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _run_celltypist(adata: Any, model: str, celltype_col: str,
                    out_dir: Path) -> dict[str, Any]:
    """CellTypist 参考注释：本地模型 + celltype_col 级 majority voting。"""
    import celltypist

    model_path = MODEL_DIR / model
    if not model_path.exists():
        avail = sorted(p.name for p in MODEL_DIR.glob("*.pkl"))
        raise ValueError(
            f"model {model!r} not found under {MODEL_DIR}; "
            f"available: {avail}")
    ref = adata.raw.to_adata()  # processed 的 raw = log-norm 全基因
    ref.obs[celltype_col] = adata.obs[celltype_col].astype(str).to_numpy()
    pred = celltypist.annotate(ref, model=str(model_path),
                               majority_voting=True,
                               over_clustering=celltype_col)
    pred_adata = pred.to_adata(insert_conf_by="majority_voting")
    labels = pred_adata.obs["majority_voting"].astype(str)
    conf = pred_adata.obs["conf_score"].astype(float)
    labels.index = adata.obs_names
    conf.index = adata.obs_names
    adata.obs["celltypist_label"] = labels
    adata.obs["celltypist_conf"] = conf

    ct = pd.crosstab(adata.obs[celltype_col].astype(str), labels)
    csv_path = out_dir / "annotate_celltypist_by_cluster.csv"
    ct.to_csv(csv_path)
    png_path = out_dir / "annotate_celltypist_umap.png"
    _umap_by_label(adata, "celltypist_label", png_path,
                   f"celltypist ({model})")
    counts = labels.value_counts()
    return {
        "label_col": "celltypist_label",
        "n_labels": int(len(counts)),
        "counts": {str(k): int(v) for k, v in counts.head(20).items()},
        "mean_conf": round(float(conf.mean()), 3),
        "csv": str(csv_path),
        "umap_png": str(png_path),
    }


def _run_markers(adata: Any, marker_sets: Any, celltype_col: str, out_col: str,
                 out_dir: Path) -> dict[str, Any]:
    """marker 基因集路：逐集打分 → 簇均值 argmax 定标签。"""
    import scanpy as sc

    if not isinstance(marker_sets, dict) or not marker_sets:
        raise ValueError(
            "marker_sets required for method=markers, e.g. "
            '{"T cell": ["CD3D", "CD3E"], "B cell": ["MS4A1"]}')
    if len(marker_sets) > 20:
        raise ValueError(f"too many marker sets: {len(marker_sets)} (>20)")
    raw_genes = (set(adata.raw.var_names.astype(str))
                 if adata.raw is not None
                 else set(adata.var_names.astype(str)))

    scored, skipped = [], []
    for name, genes in marker_sets.items():
        inter = [str(g) for g in (genes or []) if str(g) in raw_genes]
        if len(inter) < 2:
            skipped.append({"set": str(name), "found": inter})
            continue
        safe = re.sub(r"\W+", "_", str(name))
        sc.tl.score_genes(adata, inter, score_name=f"ann_{safe}",
                          use_raw=True)
        scored.append((str(name), f"ann_{safe}"))
    if not scored:
        raise ValueError(
            "all marker sets skipped (<2 genes found in data); "
            "check gene naming (symbol vs ensembl)")

    grp = adata.obs[celltype_col].astype(str)
    means = {name: adata.obs.groupby(grp, observed=True)[col].mean()
             for name, col in scored}
    M = pd.DataFrame(means)  # 簇 × 集
    assign = M.idxmax(axis=1)
    adata.obs[out_col] = grp.map(assign).astype(str)

    csv_path = out_dir / "annotate_markers_scores.csv"
    M.to_csv(csv_path, index_label=celltype_col)
    png_path = out_dir / "annotate_markers_umap.png"
    _umap_by_label(adata, out_col, png_path,
                   f"marker annotation ({len(scored)} sets)")
    counts = adata.obs[out_col].value_counts()
    return {
        "label_col": out_col,
        "n_labels": int(len(counts)),
        "counts": {str(k): int(v) for k, v in counts.head(20).items()},
        "cluster_assignment": {str(k): str(v) for k, v in assign.items()},
        "skipped_sets": skipped,
        "csv": str(csv_path),
        "umap_png": str(png_path),
    }


def main() -> None:
    """主流程：按 method 分派两路注释，结果原地写回 processed.h5ad。"""
    args = read_args()
    method = str(args.get("method", "celltypist")).strip().lower()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    out_col = str(args.get("out_col", "annotation")).strip()

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        raise ValueError(
            f"celltype column {celltype_col!r} not in obs; available: "
            f"{_cat_cols(adata)}")
    if adata.raw is None:
        raise ValueError("processed.h5ad missing raw; re-run sc_process")

    out_dir = WS_ROOT / args["dataset_id"] / "annotate"
    out_dir.mkdir(parents=True, exist_ok=True)

    if method == "celltypist":
        res = _run_celltypist(adata, str(args.get("model",
                                                  "Immune_All_Low.pkl")),
                              celltype_col, out_dir)
    elif method == "markers":
        res = _run_markers(adata, args.get("marker_sets"), celltype_col,
                           out_col, out_dir)
    else:
        raise ValueError(f"method must be celltypist or markers, "
                         f"got {method!r}")

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        **res,
        "saved": str(h5ad_path),
        "note": "标签列已写回 processed.h5ad，下游 sc_plot/sc_cellfreq/"
                "sc_cellchat 的 celltype_col 可直接引用",
    })


if __name__ == "__main__":
    run(main)
