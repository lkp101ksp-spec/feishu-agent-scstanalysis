"""st_qc：spot 级质控过滤 → filtered.h5ad + 前后统计（Phase 21）。

stdin: {"dataset_id": "...", "min_genes": 50, "max_genes": 6000,
        "max_mt_pct": 20.0}
失败消息附 genes/spot 分布（median/p90/max）指导调参（沿用 sc_qc 模式）。
"""
from __future__ import annotations

from common import emit, fail, run


def _distribution(values) -> dict:
    """genes/spot 分布摘要（失败消息调参依据）。"""
    import numpy as np

    return {
        "median": float(np.median(values)),
        "p90": float(np.percentile(values, 90)),
        "max": float(np.max(values)),
    }


def main() -> None:
    from common import WS_ROOT, load_adata, read_args

    args = read_args()
    min_genes = int(args.get("min_genes", 50))
    max_genes = int(args.get("max_genes", 6000))
    max_mt_pct = float(args.get("max_mt_pct", 20.0))

    adata = load_adata({"dataset_id": args["dataset_id"]})
    n_before = int(adata.n_obs)

    import scanpy as sc

    if "n_genes_by_counts" not in adata.obs:
        sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False,
                                   inplace=True)
    genes_per_spot = adata.obs["n_genes_by_counts"].to_numpy()
    mask = (genes_per_spot >= min_genes) & (genes_per_spot <= max_genes)
    if "pct_counts_mt" in adata.obs:
        mask &= adata.obs["pct_counts_mt"].to_numpy() <= max_mt_pct

    adata = adata[mask, :].copy()
    n_after = int(adata.n_obs)

    if n_after == 0:
        fail("SC_QC_EMPTY",
             f"all {n_before} spots filtered out (min_genes={min_genes}, "
             f"max_genes={max_genes}, max_mt_pct={max_mt_pct}); "
             f"genes/spot distribution: {_distribution(genes_per_spot)}; "
             "lower min_genes / raise max_mt_pct and retry")
        raise SystemExit(1)

    sc.pp.filter_genes(adata, min_cells=1)
    ds_dir = WS_ROOT / args["dataset_id"]
    adata.write_h5ad(ds_dir / "filtered.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_spots_before": n_before,
        "n_spots_after": n_after,
        "n_genes": int(adata.n_vars),
        "filtered_out": n_before - n_after,
    })


if __name__ == "__main__":
    run(main)
