"""st_trajectory：空间拟时序（Phase 51，sc_pseudotime 模式移植）。

stdin: {"dataset_id": ..., "root_mode": "marker", "root_marker": "",
        "root_layer": "tumor"}
需 processed.h5ad（uns['neighbors'] 表达图 + obsm['spatial']，
st_process 产物；raw=log-normalized 快照供 root_marker 提取）。
方法 scanpy diffmap + DPT（Haghverdi 2016 图扩散族）+ PAGA
（groups=spatial_domain 回退 leiden）；表达图 DPT——空间图 DPT≈BFS
距离场与 st_vicinity 语义重复，不做。
root 二选一：marker=root_marker raw 表达最高 spot（空/缺失→spot #0
+ root_note）；vicinity=obs['vicinity']==root_layer 内表达图度中位
spot（列缺失报 ST_TRAJ_NO_VICINITY）。dpt_pseudotime 写回 obs（供
st_plot 组织图叠加）并重存 processed.h5ad。
产物落 /ws/{ds}/trajectory/：pseudotime.csv + trajectory_spatial.png
（组织散点 viridis + 红圈 root）+ paga_spatial.png（域空间质心 +
连接度加权边）；obs 有 vicinity 时附 trajectory_vicinity.png
（分层 boxplot）+ emit Spearman ρ/pval（表达进程×空间分层一致性）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import (WS_ROOT, emit, ensure_spatial, fail, load_adata,
                    read_args, run)


def _rf(x: float) -> float | None:
    """round + 非有限值转 None（防 JSON 输出 NaN/Infinity）。"""
    return round(float(x), 4) if np.isfinite(x) else None


def _pick_root(adata: Any, args: dict[str, Any]) -> tuple[int, str]:
    """定根：返回 (iroot, root_note)。marker/vicinity 双模式。"""
    root_mode = str(args.get("root_mode", "marker"))
    if root_mode == "marker":
        root_marker = str(args.get("root_marker", "")).strip()
        ref = adata.raw if adata.raw is not None else adata
        if root_marker and root_marker in ref.var_names:
            x = ref[:, root_marker].X
            expr = (np.asarray(x.todense()).ravel()
                    if hasattr(x, "todense") else np.asarray(x).ravel())
            iroot = int(np.argmax(expr))
            return iroot, f"{root_marker}-highest spot #{iroot}"
        note = (f"root_marker {root_marker!r} not in data; "
                "fell back to spot #0" if root_marker
                else "root_marker empty; using spot #0")
        return 0, note
    if "vicinity" not in adata.obs:
        fail("ST_TRAJ_NO_VICINITY",
             "obs['vicinity'] 不存在；先跑 st_vicinity")
        raise SystemExit(1)
    layer = str(args.get("root_layer", "tumor"))
    vic_str = adata.obs["vicinity"].astype(str)
    mask = (vic_str == layer).to_numpy()
    if not mask.any():
        fail("INVALID_INPUT",
             f"vicinity 层 {layer!r} 无 spot（现有层: "
             f"{sorted(vic_str.unique())}）")
        raise SystemExit(1)
    conn = adata.obsp["connectivities"]
    deg = np.asarray((conn > 0).sum(axis=1)).ravel()
    idx = np.flatnonzero(mask)
    med = float(np.median(deg[idx]))
    iroot = int(idx[int(np.argmin(np.abs(deg[idx] - med)))])
    return iroot, f"vicinity {layer!r} 层度中位 spot #{iroot}"


def main() -> None:
    """主流程：定根 → diffmap → DPT → 空间/PAGA 图 → vicinity 耦合。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    root_mode = str(args.get("root_mode", "marker"))
    if root_mode not in ("marker", "vicinity"):
        fail("INVALID_INPUT",
             f"root_mode={root_mode!r} 非法（需 marker|vicinity）")
        raise SystemExit(1)

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    if "neighbors" not in adata.uns:
        fail("INVALID_INPUT",
             "processed.h5ad 缺 uns['neighbors']；先跑 st_process")
        raise SystemExit(1)

    iroot, root_note = _pick_root(adata, args)
    adata.uns["iroot"] = iroot
    sc.tl.diffmap(adata)
    sc.tl.dpt(adata)
    pt = adata.obs["dpt_pseudotime"].to_numpy(dtype=float)
    n_inf = int(np.sum(~np.isfinite(pt)))
    pt = np.where(np.isfinite(pt), pt, np.nan)  # inf（不连通）→ nan
    adata.obs["dpt_pseudotime"] = pt

    group_key = ("spatial_domain" if "spatial_domain" in adata.obs
                 else "leiden")
    groups = adata.obs[group_key].astype(str)
    stats = (pd.DataFrame({"g": groups.values, "pt": pt})
             .groupby("g")["pt"].agg(["mean", "median", "size"]))
    stats = stats.loc[sorted(stats.index, key=lambda c: (len(c), c))]

    coords = np.asarray(adata.obsm["spatial"])[:, :2]
    ds_dir = WS_ROOT / args["dataset_id"] / "trajectory"
    ds_dir.mkdir(parents=True, exist_ok=True)

    pt_csv = ds_dir / "pseudotime.csv"
    pd.DataFrame({group_key: groups.values, "dpt_pseudotime": pt},
                 index=adata.obs_names).to_csv(pt_csv)

    # 组织空间散点（viridis 着色 + 红圈 root）
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    s = ax.scatter(coords[:, 0], coords[:, 1], s=10, c=pt,
                   cmap="viridis", linewidths=0)
    ax.scatter(coords[iroot, 0], coords[iroot, 1], s=110,
               facecolors="none", edgecolors="red", linewidths=1.6,
               label="root")
    ax.legend(loc="upper right", fontsize=8)
    fig.colorbar(s, ax=ax, fraction=0.046, label="DPT pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Spatial DPT (root: {root_note})", fontsize=9)
    fig.tight_layout()
    traj_png = ds_dir / "trajectory_spatial.png"
    fig.savefig(traj_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # PAGA：域空间质心 + 连接度加权边（表达图连接度，坐标=组织空间）
    sc.tl.paga(adata, groups=group_key)
    conn = adata.uns["paga"]["connectivities"].toarray()
    cents = np.stack([coords[groups.values == c].mean(axis=0)
                      for c in stats.index])
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.scatter(coords[:, 0], coords[:, 1], s=5, c="#d9d9d9", linewidths=0)
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
    ax.set_title(f"PAGA on tissue (node = {group_key} centroid)",
                 fontsize=9)
    fig.tight_layout()
    paga_png = ds_dir / "paga_spatial.png"
    fig.savefig(paga_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    out: dict[str, Any] = {
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": "diffmap_dpt",
        "n_spots": int(adata.n_obs),
        "root_mode": root_mode,
        "root_marker": str(args.get("root_marker", "")).strip(),
        "root_cell_index": iroot,
        "root_note": root_note,
        "group_key": group_key,
        "n_disconnected": n_inf,
        "per_domain": [
            {"domain": c, "mean": _rf(r["mean"]),
             "median": _rf(r["median"]), "n_spots": int(r["size"])}
            for c, r in stats.iterrows()],
        "pseudotime_csv": str(pt_csv),
        "trajectory_png": str(traj_png),
        "paga_png": str(paga_png),
        "note": "表达邻居图 DPT（Haghverdi 2016）；dpt_pseudotime 已写回"
                " obs（st_plot 可组织图叠加）；inf=不连通 spot",
    }

    # vicinity 耦合（条件产物）：分层 boxplot + Spearman ρ
    if "vicinity" in adata.obs:
        from scipy.stats import spearmanr
        vic = adata.obs["vicinity"]
        vic_str = vic.astype(str)
        order = ([str(c) for c in vic.cat.categories]
                 if hasattr(vic, "cat") else sorted(vic_str.unique()))
        code = {c: i for i, c in enumerate(order)}
        codes = vic_str.map(code).to_numpy(dtype=float)
        ok_mask = np.isfinite(pt)
        rho, pval = spearmanr(pt[ok_mask], codes[ok_mask])
        fig, ax = plt.subplots(figsize=(5.6, 4.0))
        data = [pt[(vic_str.to_numpy() == c) & ok_mask] for c in order]
        ax.boxplot(data)
        ax.set_xticks(range(1, len(order) + 1))
        ax.set_xticklabels(order, fontsize=8)
        ax.set_xlabel("vicinity layer")
        ax.set_ylabel("DPT pseudotime")
        ax.set_title(f"pseudotime ~ vicinity (Spearman rho={rho:.3f})",
                     fontsize=9)
        fig.tight_layout()
        vic_png = ds_dir / "trajectory_vicinity.png"
        fig.savefig(vic_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        out["spearman_rho"] = _rf(float(rho))
        out["spearman_pval"] = float(pval)
        out["vicinity_png"] = str(vic_png)

    # 写回 processed.h5ad（obs['dpt_pseudotime'] 供 st_plot 叠加）
    adata.write_h5ad(WS_ROOT / args["dataset_id"] / "processed.h5ad")
    emit(out)


if __name__ == "__main__":
    run(main)
