"""st_nichenet：空间版 NicheNet 配体活性优先级（Phase 65，2026-09-18
探针 probe_st_nichenet2.py 三路判据全过后立项）。

在 sc_nichenet 基础上引入空间邻域约束：sender 不再是任意群，而是与
receiver niche 物理接壤的 spot 邻域——obs[groupby]==receiver_niche 的
spots 作 BFS core，kNN 图上逐环扩散（0=core/1..max_rings=环/-1=远端），
sender=环 1..max_rings；min_expr 门控在该收窄母体上统计，实现
"邻近才通讯"。活性排序先验驱动（aupr_corrected），空间信息的落点
=候选母体收窄（探针实测：空间侧诱饵 0/5 入选、非空间 5/5 tested）。

stdin: {"dataset_id": ..., "geneset": [...],        # receiver niche 目标基因
        "receiver_niche": "N1",                     # obs[groupby] 取值（BFS core）
        "groupby": "spatial_domain",                # 分组列
        "max_rings": 1, "knn": 6,                   # 分环参数
        "species": "",                              # 空=自动探测（六工具惯例）
        "top_n_ligands": 30, "min_expr": 0.1}       # 表达比例阈值

主链：processed.h5ad（obsm.spatial 校验）→ spatial_rings BFS 分环 →
sender/receiver 掩码表达比例统计（稀疏行 nnz 口径）→ expr_stats.csv
+ geneset.csv KB 级轻量桥接（矩阵本体不出 Python——大库免疫）→
Rscript /opt/r_tools/nichenetr_bridge.R（先验四件构建期烘焙断网可用，
零改动复用）→ 读回 ligand_activities/ligand_target_links + rings.png。

产物：nichenet_ligand_activities.csv（全量降序+rank）/
nichenet_ligand_target_links.csv（top 配体调控边）/
nichenet_ligand_bar.png（top N aupr 条图）/
nichenet_ligand_target_heatmap.png（配体×靶基因权重热图）/
rings.png（分环着色空间图——空间工具身份产物）。
out：n_spots/n_sender/n_receiver/ring_counts/n_geneset_input/
n_geneset_used/n_ligands_tested/top_ligands（top10 dict）/n_links
+ pngs 聚合键（IM/D 报告纪律）。

跨镜像分发（Phase 57 st_cellchat_v2 先例）：nichenetr R 桥单点安装在
bio 镜像，本脚本放 sc_tools（非 st_tools），经同一 WS_ROOT 卷直读 st
processed.h5ad——st 镜像零增重。
"""

from __future__ import annotations

import json
import subprocess
from collections import deque
from typing import Any

import numpy as np
import pandas as pd
from common import (
    WS_ROOT,
    detect_symbol_style,
    emit,
    fail,
    read_args,
    run,
    species_style_guard,
)

R_BRIDGE = "/opt/r_tools/nichenetr_bridge.R"


def _spatial_rings(
    coords: np.ndarray, core: np.ndarray, knn: int, max_rings: int
) -> np.ndarray:
    """空间 kNN 图上自 core 集做 BFS 分环：0=core，1..max_rings=环号，-1=远端。

    coords: (n_spots, 2) 空间坐标；core: bool 掩码（receiver niche）；
    knn: 近邻数（不含自身）；max_rings: 扩散环数上限。
    """
    from scipy.spatial import cKDTree

    _, idx = cKDTree(coords).query(coords, k=knn + 1)
    nbrs = idx[:, 1:]
    lab = np.full(len(coords), -1, dtype=int)
    lab[core] = 0
    q: deque[int] = deque(int(i) for i in np.where(core)[0])
    r = 0
    while q and r < max_rings:
        for _ in range(len(q)):
            i = q.popleft()
            for j in nbrs[i]:
                if lab[j] == -1:
                    lab[j] = r + 1
                    q.append(int(j))
        r += 1
    return lab


def _pct(x: Any, mask: np.ndarray) -> np.ndarray:
    """稀疏/稠密通用：掩码 spot 中每基因表达（>0）比例，行=基因。"""
    sub = x[mask, :]
    if hasattr(sub, "tocsr"):
        sub = sub.tocsr()
        counts: np.ndarray = np.asarray((sub != 0).sum(axis=0)).ravel().astype(float)
        return counts / int(sub.shape[0])
    frac: np.ndarray = np.asarray((np.asarray(sub) != 0).mean(axis=0)).ravel()
    return np.asarray(frac, dtype=np.float64)


def main() -> None:
    args = read_args()
    ds = str(args["dataset_id"])
    geneset = [str(g) for g in args.get("geneset", [])]
    receiver_niche = str(args.get("receiver_niche", ""))
    groupby = str(args.get("groupby", "spatial_domain"))
    max_rings = int(args.get("max_rings", 1))
    knn = int(args.get("knn", 6))
    species = str(args.get("species", ""))
    top_n = int(args.get("top_n_ligands", 30))
    min_expr = float(args.get("min_expr", 0.1))
    ds_dir = WS_ROOT / ds

    if not geneset:
        fail(
            "INVALID_INPUT",
            "geneset is required (receiver-side target genes, "
            "e.g. DE/upregulated genes of the receiver niche)",
        )
        return
    if not receiver_niche:
        fail(
            "INVALID_INPUT",
            "receiver_niche is required (a value of obs groupby column "
            "defining the receiver niche as BFS core)",
        )
        return
    if not 1 <= max_rings <= 5:
        fail("INVALID_INPUT", f"max_rings must be in [1, 5] (got {max_rings})")
        return
    if knn < 2:
        fail("INVALID_INPUT", f"knn must be >= 2 (got {knn})")
        return

    p = ds_dir / "processed.h5ad"
    if not p.exists():
        fail(
            "INVALID_INPUT",
            f"no processed.h5ad under dataset {ds!r} — run st_process "
            "first (need obsm.spatial + obs groups + normalized X)",
        )
        return
    import anndata as ad

    aw = ad.read_h5ad(p)
    if "spatial" not in aw.obsm:
        fail(
            "ST_FORMAT_INVALID",
            "processed.h5ad lacks obsm['spatial'] — run st_process "
            "(spatial coordinates required for niche rings)",
        )
        return
    coords: np.ndarray = np.asarray(aw.obsm["spatial"], dtype=float)
    x: Any = aw.X
    if x is not None and x.size and float(x.min()) < 0:  # scaled → 回退 .raw
        if aw.raw is not None:
            x = aw.raw.X
        else:
            fail(
                "INVALID_INPUT",
                "processed.h5ad X appears scaled (negative values) "
                "with no .raw fallback — cannot compute expression "
                "percentages",
            )
            return
    genes = pd.Index(aw.var_names.astype(str))
    if aw.raw is not None and x is aw.raw.X:
        genes = pd.Index(aw.raw.var_names.astype(str))

    if groupby not in aw.obs:
        fail(
            "ST_NICHENET_NO_GROUP",
            f"groupby column {groupby!r} not in obs; "
            f"available: {list(aw.obs.columns)[:20]}",
        )
        return
    cl = aw.obs[groupby].astype(str).to_numpy()
    if receiver_niche not in set(cl):
        vals = pd.unique(cl)
        fail(
            "ST_NICHENET_NO_GROUP",
            f"receiver_niche {receiver_niche!r} not found in "
            f"{groupby!r}; values: {list(vals)[:25]}",
        )
        return

    core = cl == receiver_niche
    rings = _spatial_rings(coords, core, knn, max_rings)
    rmask = core
    smask = (rings >= 1) & (rings <= max_rings)
    if not smask.any():
        fail(
            "INVALID_INPUT",
            f"no sender spots within {max_rings} ring(s) of niche "
            f"{receiver_niche!r} (niche too compact at knn={knn}) — "
            "increase max_rings or knn",
        )
        return

    # 物种：空=自动探测（记忆库六工具惯例）+ 风格矛盾快败
    if not species:
        style = detect_symbol_style(genes)
        species = "mouse" if style == "title" else "human"
    if species not in ("mouse", "human"):
        fail(
            "INVALID_INPUT",
            f"species must be 'mouse' or 'human' (got {species!r}; empty = auto-detect)",
        )
        return
    species_style_guard(species, genes, "INVALID_INPUT")

    in_dir = ds_dir / "_nn_in"
    in_dir.mkdir(parents=True, exist_ok=True)
    stats = pd.DataFrame(
        {
            "gene": genes,
            "sender_pct": np.round(_pct(x, smask), 5),
            "receiver_pct": np.round(_pct(x, rmask), 5),
        }
    )
    stats.to_csv(in_dir / "expr_stats.csv", index=False)
    pd.Series(geneset).to_csv(in_dir / "geneset.csv", index=False, header=False)

    r_cmd = ["Rscript", R_BRIDGE, str(in_dir), species, str(top_n), str(min_expr)]
    proc = subprocess.run(r_cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        fail("SCRIPT_ERROR", f"Rscript nichenetr_bridge.R failed: {proc.stderr[-1200:]}")
        return
    meta_p = in_dir / "nn_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}

    used = int(meta.get("n_geneset_used", 0))
    tested = int(meta.get("n_ligands_tested", 0))
    if used < 5:
        fail(
            "INVALID_INPUT",
            f"only {used}/{len(geneset)} geneset genes exist in the "
            f"{species} prior target space — check species (symbol "
            "style) or geneset source",
        )
        return
    if tested < 3:
        fail(
            "INVALID_INPUT",
            f"only {tested} ligands testable — sender ring(s) of niche "
            f"{receiver_niche!r} express too few prior ligands (lower "
            "min_expr, increase max_rings, or check groupby)",
        )
        return

    act = pd.read_csv(in_dir / "nn_ligand_activities.csv")
    links_p = in_dir / "nn_ligand_target_links.csv"
    links = (
        pd.read_csv(links_p)
        if links_p.exists()
        else pd.DataFrame(columns=["test_ligand", "target", "weight"])
    )
    act_csv = str(ds_dir / "nichenet_ligand_activities.csv")
    act.round(4).to_csv(act_csv, index=False)
    links_csv = str(ds_dir / "nichenet_ligand_target_links.csv")
    links.to_csv(links_csv, index=False)

    import matplotlib.pyplot as plt

    head = act.head(min(top_n, len(act)))
    fig, ax = plt.subplots(figsize=(5.6, 0.3 * len(head) + 1.2))
    ax.barh(head["test_ligand"][::-1], head["aupr_corrected"][::-1], color="#4c72b0")
    ax.set_xlabel("aupr_corrected")
    ax.set_title(f"NicheNet ligand activities ({species}, spatial)", fontsize=9)
    fig.tight_layout()
    bar_png = str(ds_dir / "nichenet_ligand_bar.png")
    fig.savefig(bar_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    heat_png = ""
    if len(links):
        lcol = "test_ligand" if "test_ligand" in links.columns else links.columns[0]
        tcol = "target" if "target" in links.columns else links.columns[1]
        wcol = "weight" if "weight" in links.columns else links.columns[-1]
        top_targets = links.groupby(tcol)[wcol].max().sort_values(ascending=False).head(40).index
        pv = links[links[tcol].isin(top_targets)].pivot_table(
            index=lcol, columns=tcol, values=wcol, fill_value=0.0
        )
        order = [g for g in head["test_ligand"] if g in pv.index]
        pv = pv.reindex(order)
        fig, ax = plt.subplots(figsize=(0.22 * pv.shape[1] + 2.6, 0.32 * len(pv) + 1.4))
        im = ax.imshow(pv.to_numpy(), aspect="auto", cmap="Blues")
        ax.set_xticks(range(pv.shape[1]))
        ax.set_xticklabels(pv.columns, rotation=90, fontsize=6)
        ax.set_yticks(range(len(pv)))
        ax.set_yticklabels(pv.index, fontsize=7)
        fig.colorbar(im, ax=ax, fraction=0.03, label="weight")
        ax.set_title("Ligand-target links (top ligands)", fontsize=9)
        fig.tight_layout()
        heat_png = str(ds_dir / "nichenet_ligand_target_heatmap.png")
        fig.savefig(heat_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    rings_png = str(ds_dir / "rings.png")
    fig, ax = plt.subplots(figsize=(6.0, 6.0))
    rorder = np.argsort(rings)  # 远端先画，core 最后压顶
    ax.scatter(coords[rorder, 0], coords[rorder, 1], c=rings[rorder],
               cmap="Oranges", vmin=-1, s=4, linewidths=0)
    ax.invert_yaxis()
    ax.set_title(f"BFS rings around niche {receiver_niche!r} "
                 f"(core={int(core.sum())}, sender={int(smask.sum())})",
                 fontsize=9)
    ax.set_xlabel("spatial-1")
    ax.set_ylabel("spatial-2")
    fig.tight_layout()
    fig.savefig(rings_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top10 = {str(r.test_ligand): round(float(r.aupr_corrected), 4) for r in head.head(10).itertuples()}
    ring_counts = {str(k): int(v) for k, v in zip(*np.unique(rings, return_counts=True))}
    emit(
        {
            "ok": True,
            "species": species,
            "groupby": groupby,
            "receiver_niche": receiver_niche,
            "n_spots": int(len(coords)),
            "n_sender": int(smask.sum()),
            "n_receiver": int(rmask.sum()),
            "ring_counts": ring_counts,
            "n_geneset_input": len(geneset),
            "n_geneset_used": used,
            "n_ligands_tested": tested,
            "top_ligands": top10,
            "n_links": int(len(links)),
            "ligand_activities_csv": act_csv,
            "ligand_target_links_csv": links_csv,
            "ligand_bar_png": bar_png,
            "ligand_target_heatmap_png": heat_png,
            "rings_png": rings_png,
            "pngs": [p for p in [bar_png, heat_png, rings_png] if p],
        }
    )


run(main)
