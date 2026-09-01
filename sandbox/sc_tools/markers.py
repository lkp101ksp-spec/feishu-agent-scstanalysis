"""sc_markers：每簇差异基因（rank_genes_groups）+ dotplot（Phase 20）。

stdin: {"dataset_id": ..., "method": "wilcoxon", "top_n": 10}
需 processed.h5ad（无则报错提示先跑 sc_process）。
"""
from __future__ import annotations

from common import WS_ROOT, emit, load_adata, run, read_args


def main() -> None:
    import matplotlib.pyplot as plt
    import pandas as pd
    import scanpy as sc

    args = read_args()
    method = str(args.get("method", "wilcoxon"))
    top_n = int(args.get("top_n", 10))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})

    # 差异分析用原始计数尺度（adata.raw：归一化 log 后全基因快照）
    sc.tl.rank_genes_groups(adata, groupby="leiden", method=method,
                            use_raw=True)

    # 每簇 top 基因（名字/score/logFC/pct）
    result = adata.uns["rank_genes_groups"]
    groups = [str(g) for g in result["names"].dtype.names]
    markers = {}
    for g in groups:
        genes = result["names"][g][:top_n]
        scores = result["scores"][g][:top_n]
        logfc = result["logfoldchanges"][g][:top_n]
        markers[g] = [
            {"gene": str(gn), "score": round(float(s), 2),
             "log2fc": round(float(lf), 2)}
            for gn, s, lf in zip(genes, scores, logfc)
        ]

    # dotplot 图（top5/簇，标签天然英文）
    fig = sc.pl.rank_genes_groups_dotplot(
        adata, n_genes=5, show=False, return_fig=True,
        standard_scale="var")
    ds_dir = WS_ROOT / args["dataset_id"]
    dotplot_png = ds_dir / "dotplot.png"
    fig.savefig(dotplot_png, bbox_inches="tight")
    plt.close("all")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        "n_clusters": len(groups),
        "markers": markers,
        "dotplot_png": str(dotplot_png),
    })


if __name__ == "__main__":
    run(main)
