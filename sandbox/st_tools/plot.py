"""st_plot：基因表达/域着色 spatial 图 → png 列表（Phase 21）。

stdin: {"dataset_id": "...", "genes": [...], "color_by": "spatial_domain"}
genes 模式每基因一张 spatial 表达着色图；color_by 模式按 obs 列着色。
"""
from __future__ import annotations

from common import emit, fail, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, load_adata, read_args

    args = read_args()
    genes = list(args.get("genes") or [])
    color_by = args.get("color_by", "")

    adata = load_adata({"dataset_id": args["dataset_id"]})
    ensure_spatial(adata)

    import matplotlib.pyplot as plt
    import squidpy as sq

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs: list[str] = []

    if genes:
        valid = [g for g in genes if g in adata.var_names]
        if not valid:
            fail("ST_GENES_NOT_FOUND",
                 f"none of genes {genes} found in dataset "
                 f"({adata.n_vars} genes)")
            raise SystemExit(1)
        for g in valid:
            fig = sq.pl.spatial_scatter(adata, color=g, show=False,
                                        return_fig=True)
            fig.savefig(ds_dir / f"{g}_spatial.png", dpi=150,
                        bbox_inches="tight")
            plt.close("all")
            pngs.append(f"/ws/{args['dataset_id']}/{g}_spatial.png")

    if color_by:
        if color_by not in adata.obs:
            fail("ST_COLOR_BY_NOT_FOUND",
                 f"color_by {color_by!r} not in obs columns "
                 f"{list(adata.obs.columns)}; run st_process first "
                 "for spatial_domain")
            raise SystemExit(1)
        fig = sq.pl.spatial_scatter(adata, color=color_by, show=False,
                                    return_fig=True)
        fig.savefig(ds_dir / f"{color_by}_spatial.png", dpi=150,
                    bbox_inches="tight")
        plt.close("all")
        pngs.append(f"/ws/{args['dataset_id']}/{color_by}_spatial.png")

    if not pngs:
        fail("INVALID_INPUT", "provide genes or color_by (at least one)")
        raise SystemExit(1)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "pngs": pngs,
    })


if __name__ == "__main__":
    run(main)
