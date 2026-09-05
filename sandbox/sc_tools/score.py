"""sc_score_genes：基因集打分（Phase 32，对齐 toolsv1 server_gene_set_scoring）。

stdin: {"dataset_id": ...,
        "gene_sets": {"Cytotoxic": ["GZMB", "PRF1"], "Exhausted": ["PDCD1"]}}
需 processed.h5ad（sc_process 产物）。
每基因集 sc.tl.score_genes（均值差打分，等价 Seurat AddModuleScore）；
基因与 raw 求交，空交集跳过（全部为空时报错）。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run

MAX_SETS = 8


def _sanitize(name: str, used: set[str]) -> str:
    """基因集名 → 合法 obs 列名（[A-Za-z0-9_]、≤40 字符、防冲突）。"""
    col = re.sub(r"\W+", "_", str(name)).strip("_")[:40] or "GeneSet"
    base, i = col, 1
    while col in used:
        i += 1
        col = f"{base}_{i}"
    used.add(col)
    return col


def main() -> None:
    """主流程：逐基因集 score_genes → 按簇统计 + UMAP/小提琴图 + 分数 csv。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    gene_sets: dict[str, list[str]] = dict(args.get("gene_sets") or {})
    if not gene_sets:
        raise ValueError("gene_sets is required: {'SetName': ['GENE1', ...]}")
    if len(gene_sets) > MAX_SETS:
        raise ValueError(f"too many gene sets ({len(gene_sets)} > {MAX_SETS})")
    for name, genes in gene_sets.items():
        if (not isinstance(genes, list)
                or not all(isinstance(g, str) for g in genes)):
            raise ValueError(
                f"gene_sets[{name!r}] must be a list of gene symbols")

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    if "leiden" not in adata.obs or "X_umap" not in adata.obsm:
        raise ValueError("processed.h5ad lacks leiden/X_umap; "
                         "run sc_process first")

    raw_vars = set(adata.raw.var_names)
    clusters = adata.obs["leiden"].astype(str)
    used: set[str] = set()
    results: list[dict] = []
    skipped: list[str] = []

    for name, genes in gene_sets.items():
        matched = [g for g in genes if g in raw_vars]
        if not matched:
            if len(gene_sets) == 1:
                raise ValueError(
                    f"gene set {name!r}: no genes found in data "
                    "(check symbols, e.g. human uppercase)")
            skipped.append(name)
            continue
        col = _sanitize(name, used)
        sc.tl.score_genes(adata, gene_list=matched, score_name=col,
                          use_raw=True)
        stats = (pd.DataFrame({"cluster": clusters.values,
                               "score": adata.obs[col].astype(float)})
                 .groupby("cluster")["score"]
                 .agg(["mean", "median", "size"])
                 .sort_values("mean", ascending=False))
        results.append({
            "name": name, "score_col": col,
            "n_input": len(genes), "n_used": len(matched),
            "per_cluster": [
                {"cluster": c, "mean": round(float(r["mean"]), 4),
                 "median": round(float(r["median"]), 4),
                 "n_cells": int(r["size"])}
                for c, r in stats.iterrows()],
        })

    if not results:
        raise ValueError(f"all gene sets empty after matching: {skipped}")

    ds_dir = WS_ROOT / args["dataset_id"] / "score"
    ds_dir.mkdir(parents=True, exist_ok=True)

    # 细胞 × 基因集 分数表
    score_df = adata.obs[[r["score_col"] for r in results]].copy()
    score_df.insert(0, "leiden", clusters.values)
    scores_csv = ds_dir / "gene_set_scores.csv"
    score_df.to_csv(scores_csv)

    # UMAP 着色（每基因集一子图）
    umap = np.asarray(adata.obsm["X_umap"])
    n = len(results)
    ncol = min(n, 4)
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.6 * ncol, 3.8 * nrow),
                             squeeze=False)
    for i, r in enumerate(results):
        ax = axes[i // ncol][i % ncol]
        s = ax.scatter(umap[:, 0], umap[:, 1], s=4,
                       c=adata.obs[r["score_col"]].to_numpy(dtype=float),
                       cmap="viridis", linewidths=0)
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(f"{r['name']} ({r['n_used']} genes)", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    fig.tight_layout()
    umap_png = ds_dir / "score_umap.png"
    fig.savefig(umap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 按簇小提琴（每基因集一子图）
    order = sorted(clusters.unique(), key=lambda c: (len(c), c))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.6 * ncol, 3.4 * nrow),
                             squeeze=False)
    for i, r in enumerate(results):
        ax = axes[i // ncol][i % ncol]
        data = [adata.obs.loc[clusters == c, r["score_col"]]
                .to_numpy(dtype=float) for c in order]
        ax.violinplot(data, showmedians=True)
        ax.set_xticks(range(1, len(order) + 1))
        ax.set_xticklabels(order, fontsize=8)
        ax.set_title(r["name"], fontsize=9)
        ax.set_ylabel("score")
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    fig.tight_layout()
    violin_png = ds_dir / "score_violin.png"
    fig.savefig(violin_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_cells": int(adata.n_obs),
        "gene_sets": results,
        "skipped": skipped,
        "scores_csv": str(scores_csv),
        "umap_png": str(umap_png),
        "violin_png": str(violin_png),
    })


if __name__ == "__main__":
    run(main)
