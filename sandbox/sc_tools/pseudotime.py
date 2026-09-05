"""sc_pseudotime：扩散伪时序（Phase 32，对齐 toolsv1 server_pseudotime 的 DPT 分支）。

stdin: {"dataset_id": ..., "root_marker": "NKG7"}
需 processed.h5ad（含 neighbors 图）。方法 scanpy diffmap + DPT
（Haghverdi 2016 图扩散族，覆盖 Monocle 拟时序的排序场景；
分支推断/BEAM/CytoTRACE2 不在本工具范围）。
root 细胞 = root_marker 在 raw 中表达最高的细胞；
root_marker 空或不在数据中则取第 0 个细胞（结果中说明）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def _rf(x: float) -> float | None:
    """round + 非有限值转 None（防 JSON 输出 NaN/Infinity）。"""
    return round(float(x), 4) if np.isfinite(x) else None


def main() -> None:
    """主流程：diffmap → 定根 → DPT → UMAP/PAGA 图 + 每簇均值表。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    root_marker = str(args.get("root_marker", "")).strip()

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    if "leiden" not in adata.obs or "X_umap" not in adata.obsm:
        raise ValueError("processed.h5ad lacks leiden/X_umap; "
                         "run sc_process first")
    if "neighbors" not in adata.uns:
        raise ValueError("processed.h5ad lacks neighbors graph; "
                         "run sc_process first")

    # 定根：root_marker raw 表达最高的细胞（兼容稀疏/稠密 X）
    root_note = ""
    if root_marker and root_marker in adata.raw.var_names:
        x = adata.raw[:, root_marker].X
        expr = (np.asarray(x.todense()).ravel() if hasattr(x, "todense")
                else np.asarray(x).ravel())
        iroot = int(np.argmax(expr))
        root_note = f"{root_marker}-highest cell #{iroot}"
    else:
        iroot = 0
        if root_marker:
            root_note = (f"root_marker {root_marker!r} not in data; "
                         "fell back to cell #0")
        else:
            root_note = "root_marker empty; using cell #0"
    adata.uns["iroot"] = iroot

    sc.tl.diffmap(adata)
    sc.tl.dpt(adata)
    pt = adata.obs["dpt_pseudotime"].to_numpy(dtype=float)
    n_inf = int(np.sum(~np.isfinite(pt)))
    pt = np.where(np.isfinite(pt), pt, np.nan)  # inf（不连通）→ nan

    clusters = adata.obs["leiden"].astype(str)
    stats = (pd.DataFrame({"cluster": clusters.values, "pt": pt})
             .groupby("cluster")["pt"]
             .agg(["mean", "median", "size"]))
    stats = stats.loc[sorted(stats.index, key=lambda c: (len(c), c))]

    ds_dir = WS_ROOT / args["dataset_id"] / "pseudotime"
    ds_dir.mkdir(parents=True, exist_ok=True)

    pt_df = pd.DataFrame({"leiden": clusters.values, "dpt_pseudotime": pt},
                         index=adata.obs_names)
    pt_csv = ds_dir / "pseudotime.csv"
    pt_df.to_csv(pt_csv)

    # UMAP 伪时序着色（红圈 = root）
    umap = np.asarray(adata.obsm["X_umap"])
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=pt, cmap="viridis",
                   linewidths=0)
    ax.scatter(umap[iroot, 0], umap[iroot, 1], s=90, facecolors="none",
               edgecolors="red", linewidths=1.6, label="root")
    ax.legend(loc="upper right", fontsize=8)
    fig.colorbar(s, ax=ax, fraction=0.046, label="DPT pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Diffusion pseudotime (root: {root_note})", fontsize=9)
    fig.tight_layout()
    umap_png = ds_dir / "pseudotime_umap.png"
    fig.savefig(umap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # PAGA 图：簇 UMAP 质心 + 连接度加权边
    sc.tl.paga(adata, groups="leiden")
    conn = adata.uns["paga"]["connectivities"].toarray()
    cents = np.stack([umap[clusters.values == c].mean(axis=0)
                      for c in stats.index])
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    ax.scatter(umap[:, 0], umap[:, 1], s=3, c="#d9d9d9", linewidths=0)
    w = conn[conn > 0]
    vmax = float(w.max()) if w.size else 1.0
    for i in range(len(cents)):
        for j in range(i + 1, len(cents)):
            if conn[i, j] > 0:
                ax.plot(*zip(cents[i], cents[j]), color="#3b7dd8",
                        lw=0.6 + 2.4 * conn[i, j] / vmax, alpha=0.75,
                        zorder=2)
    ax.scatter(cents[:, 0], cents[:, 1], s=60, c="#f2a636",
               edgecolors="black", linewidths=0.6, zorder=3)
    for (x, y), c in zip(cents, stats.index):
        ax.annotate(c, (x, y), fontsize=8, ha="center", va="center",
                    zorder=4)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("PAGA (node = leiden cluster centroid)", fontsize=9)
    fig.tight_layout()
    paga_png = ds_dir / "paga_graph.png"
    fig.savefig(paga_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": "diffmap_dpt",
        "n_cells": int(adata.n_obs),
        "root_marker": root_marker,
        "root_cell_index": iroot,
        "root_note": root_note,
        "n_disconnected": n_inf,
        "per_cluster": [
            {"cluster": c, "mean": _rf(r["mean"]), "median": _rf(r["median"]),
             "n_cells": int(r["size"])}
            for c, r in stats.iterrows()],
        "pseudotime_csv": str(pt_csv),
        "umap_png": str(umap_png),
        "paga_png": str(paga_png),
    })


if __name__ == "__main__":
    run(main)
