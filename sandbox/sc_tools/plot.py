"""sc_plot：指定基因小提琴图 / UMAP 基因着色图（Phase 20）。

stdin: {"dataset_id": ..., "genes": ["CD3D", ...], "kind": "violin"|"umap_gene"}
需 processed.h5ad（无则报错提示先跑 sc_process）。
"""
from __future__ import annotations

from common import WS_ROOT, emit, fail, load_adata, run, read_args


def main() -> None:
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    genes = list(args.get("genes") or [])
    kind = str(args.get("kind", "violin"))
    if not genes:
        fail("INVALID_INPUT", "genes list is empty")
        return
    if len(genes) > 6:
        fail("INVALID_INPUT", f"too many genes ({len(genes)}), max 6 per call")
        return

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})

    # 基因存在性校验（raw 快照含全基因）
    available = set(adata.raw.var_names) if adata.raw is not None else \
        set(adata.var_names)
    missing = [g for g in genes if g not in available]
    if missing:
        fail("SC_GENE_NOT_FOUND",
             f"genes not in dataset: {', '.join(missing)}")
        return

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs = []
    for gene in genes:
        fig, ax = plt.subplots(figsize=(6, 4.5), dpi=150)
        if kind == "violin":
            sc.pl.violin(adata, gene, groupby="leiden", ax=ax, show=False,
                         use_raw=True)
        else:  # umap_gene
            sc.pl.umap(adata, color=gene, ax=ax, show=False, use_raw=True,
                       title=f"UMAP — {gene}")
        png = ds_dir / f"{gene}_{'violin' if kind == 'violin' else 'umap'}.png"
        fig.savefig(png, bbox_inches="tight")
        plt.close(fig)
        pngs.append(str(png))

    emit({"ok": True, "dataset_ref": args["dataset_id"], "kind": kind,
          "pngs": pngs})


if __name__ == "__main__":
    run(main)
