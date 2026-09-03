"""sc_markers：每簇差异基因（rank_genes_groups）+ dotplot（Phase 20/25）。

stdin: {"dataset_id": ..., "method": "wilcoxon", "top_n": 10}
需 processed.h5ad（无则报错提示先跑 sc_process）。
Phase 25：GPU 镜像走 rsc.tl.rank_genes_groups（use_raw=False，HVG 尺度）；
CPU 镜像维持 use_raw=True（归一化 log 全基因快照）。
"""
from __future__ import annotations

from common import WS_ROOT, emit, load_adata, run, read_args


def main() -> None:
    """主流程：import 探测 rapids_singlecell 决定 GPU/CPU 分支。"""
    import matplotlib.pyplot as plt
    import pandas as pd
    import scanpy as sc

    try:
        import rapids_singlecell as rsc
        gpu = True
    except ImportError:
        rsc = None
        gpu = False

    args = read_args()
    method = str(args.get("method", "wilcoxon"))
    top_n = int(args.get("top_n", 10))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})

    if gpu:
        # rsc 不支持 use_raw=True；HVG 尺度差异分析（skill 实战记录）
        rsc.tl.rank_genes_groups(adata, groupby="leiden", method=method,
                                 use_raw=False)
    else:
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
        "accelerator": "gpu" if gpu else "cpu",
        "dotplot_png": str(dotplot_png),
    })


if __name__ == "__main__":
    run(main)
