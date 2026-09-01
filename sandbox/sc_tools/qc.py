"""sc_qc：基因/细胞/线粒体过滤 → filtered.h5ad + 前后统计（Phase 20）。

stdin: {"dataset_id": ..., "min_genes": 600, "min_cells": 3,
        "max_mt_pct": 20}
"""
from __future__ import annotations

from common import WS_ROOT, emit, fail, load_adata, run, read_args


def main() -> None:
    import scanpy as sc

    args = read_args()
    min_genes = int(args.get("min_genes", 600))
    min_cells = int(args.get("min_cells", 3))
    max_mt_pct = float(args.get("max_mt_pct", 20.0))

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "any"})
    n_before_cells, n_before_genes = adata.n_obs, adata.n_vars

    # 过滤阈值诊断依据（raw 全量分布，失败消息里指导调参）
    import numpy as np

    genes_per_cell = np.asarray((adata.X > 0).sum(axis=1)).ravel()

    # 线粒体过滤（无 mt 标记时跳过该条件）
    var_names = adata.var_names.astype(str)
    is_mt = var_names.str.startswith("MT-") | var_names.str.startswith("mt-")
    if is_mt.any() and max_mt_pct < 100:
        import numpy as np

        mt_counts = np.asarray(adata[:, is_mt].X.sum(axis=1)).ravel()
        total = np.asarray(adata.X.sum(axis=1)).ravel()
        mt_pct = np.where(total > 0, mt_counts / total * 100.0, 0.0)
        adata = adata[mt_pct <= max_mt_pct].copy()

    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_genes(adata, min_cells=min_cells)

    if adata.n_obs == 0 or adata.n_vars == 0:
        fail("SC_QC_OVERFILTERED",
             f"all cells/genes filtered out (min_genes={min_genes}, "
             f"min_cells={min_cells}, max_mt_pct={max_mt_pct}); "
             f"dataset genes/cell: median={np.median(genes_per_cell):.0f} "
             f"p90={np.percentile(genes_per_cell, 90):.0f} "
             f"max={genes_per_cell.max():.0f}; "
             "retry with min_genes below the median")
        return

    ds_dir = WS_ROOT / args["dataset_id"]
    adata.write_h5ad(ds_dir / "filtered.h5ad")
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_cells_before": int(n_before_cells),
        "n_genes_before": int(n_before_genes),
        "n_cells_after": int(adata.n_obs),
        "n_genes_after": int(adata.n_vars),
        "cells_removed": int(n_before_cells - adata.n_obs),
        "genes_removed": int(n_before_genes - adata.n_vars),
    })


if __name__ == "__main__":
    run(main)
