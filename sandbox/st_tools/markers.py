"""st_markers：空间域差异基因 → markers.json + dotplot.png（Phase 21）。

stdin: {"dataset_id": "...", "method": "wilcoxon", "top_n": 10}
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, fail, load_adata, read_args

    args = read_args()
    method = args.get("method", "wilcoxon")
    top_n = int(args.get("top_n", 10))

    adata = load_adata({"dataset_id": args["dataset_id"]})
    if "spatial_domain" not in adata.obs:
        fail("ST_STATE_INVALID",
             "spatial_domain not found; run st_process first")
        raise SystemExit(1)

    import matplotlib.pyplot as plt
    import scanpy as sc

    sc.tl.rank_genes_groups(adata, groupby="spatial_domain", method=method)
    result = adata.uns["rank_genes_groups"]
    groups = [g for g in result["names"].dtype.names]
    markers = {}
    for g in groups:
        rows = []
        for i in range(min(top_n, len(result["names"][g]))):
            rows.append({
                "gene": str(result["names"][g][i]),
                "score": round(float(result["scores"][g][i]), 2),
                "log2fc": round(float(result["logfoldchanges"][g][i]), 2),
            })
        markers[str(g)] = rows

    top_genes = [markers[g][0]["gene"] for g in groups if markers[g]][:6]
    ds_dir = WS_ROOT / args["dataset_id"]
    if top_genes:
        dp = sc.pl.dotplot(adata, top_genes, groupby="spatial_domain",
                           show=False, return_fig=True)
        dp.savefig(ds_dir / "dotplot.png", dpi=150, bbox_inches="tight")
        plt.close("all")

    import json

    (ds_dir / "markers.json").write_text(
        json.dumps(markers, ensure_ascii=False, indent=2), encoding="utf-8")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_groups": len(groups),
        "markers": markers,
        "dotplot_png": f"/ws/{args['dataset_id']}/dotplot.png",
    })


if __name__ == "__main__":
    run(main)
