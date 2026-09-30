"""sc_plot：基因小提琴 / UMAP 基因着色 / obs 列 UMAP 着色（Phase 20/58）。

stdin: {"dataset_id": ..., "genes": ["CD3D", ...],
        "kind": "violin"|"umap_gene"|"umap_obs",
        "obs_cols": ["slingshot_lineage", ...],
        "min_dist": 0.8, "spread": 1.5}
kind=umap_obs 时读 obs_cols（≤6，须存在于 obs.columns），genes 可空；
需 processed.h5ad（无则报错提示先跑 sc_process）。
min_dist/spread 任一提供（仅 umap 类 kind）时先按新参数重算 UMAP
嵌入并写回 processed.h5ad 再绘图（2026-09-30 追问调参缺口：
"umap 再松散一些"= min_dist↑，默认 0.5，松散可试 0.8~0.99）。
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
    # UMAP 松散度调参（2026-09-30）：任一提供则重算嵌入并写回
    recompute: dict[str, float] = {}
    if kind in ("umap_obs", "umap_gene"):
        if args.get("min_dist") is not None:
            recompute["min_dist"] = float(args["min_dist"])
        if args.get("spread") is not None:
            recompute["spread"] = float(args["spread"])
    if recompute:
        if "neighbors" not in adata.uns:
            fail("INVALID_INPUT",
                 "dataset has no neighbors graph; "
                 "run sc_process/sc_integrate first")
            return
        sc.tl.umap(adata, **recompute)
        adata.write_h5ad(ds_dir / "processed.h5ad")
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
              "pngs": pngs,
              **({"umap_recomputed": recompute} if recompute else {})})
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
