"""sc_plot：基因小提琴 / UMAP 基因着色 / obs 列 UMAP 着色（Phase 20/58）。

stdin: {"dataset_id": ..., "genes": ["CD3D", ...],
        "kind": "violin"|"umap_gene"|"umap_obs",
        "obs_cols": ["slingshot_lineage", ...]}
kind=umap_obs 时读 obs_cols（≤6，须存在于 obs.columns），genes 可空；
需 processed.h5ad（无则报错提示先跑 sc_process）。
"""
from __future__ import annotations

from common import WS_ROOT, emit, fail, load_adata, read_args, run


def main() -> None:
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    genes = list(args.get("genes") or [])
    kind = str(args.get("kind", "violin"))
    obs_cols = [str(c) for c in (args.get("obs_cols") or [])]
    if kind == "umap_obs":
        # obs 列 UMAP 着色（Phase 58）：类别/连续列 sc.pl.umap 自适应
        if not obs_cols:
            fail("INVALID_INPUT", "kind=umap_obs requires non-empty obs_cols")
            return
        if len(obs_cols) > 6:
            fail("INVALID_INPUT",
                 f"too many obs_cols ({len(obs_cols)}), max 6 per call")
            return
    else:
        if not genes:
            fail("INVALID_INPUT", "genes list is empty")
            return
        if len(genes) > 6:
            fail("INVALID_INPUT",
                 f"too many genes ({len(genes)}), max 6 per call")
            return

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})

    if kind == "umap_obs":
        missing = [c for c in obs_cols if c not in adata.obs.columns]
        if missing:
            fail("INVALID_INPUT",
                 f"obs_cols not in obs: {', '.join(missing)}")
            return
    else:
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
    if kind == "umap_obs":
        for col in obs_cols:
            fig, ax = plt.subplots(figsize=(6, 4.5), dpi=150)
            sc.pl.umap(adata, color=col, ax=ax, show=False,
                       title=f"UMAP — {col}")
            png = ds_dir / f"{col}_umap.png"
            fig.savefig(png, bbox_inches="tight")
            plt.close(fig)
            pngs.append(str(png))
        emit({"ok": True, "dataset_ref": args["dataset_id"], "kind": kind,
              "pngs": pngs})
        return
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
