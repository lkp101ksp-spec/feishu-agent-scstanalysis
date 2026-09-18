"""sc_nichenet：NicheNet 配体活性优先级（独立工具，2026-09-18 八补记
q2 探针终判落地：Browaeys et al. 2020 Nat Methods，nichenetr v2）。

回答"哪些配体最可能调控 receiver 细胞中我感兴趣的基因集"——与
CellChat 互补（后者机制证据库强、本工具先验调控网络强；配体活性
aupr_corrected 排序，跨配体可比）。

stdin: {"dataset_id": ..., "geneset": [...],        # receiver 目标基因
        "groupby": "celltype",                      # obs 分组列
        "sender_groups": ["CAF", ...],              # 发送者群
        "receiver_groups": [],                      # 接收者群（空=全部）
        "species": "",                              # 空=自动探测（六工具惯例）
        "top_n_ligands": 20, "min_expr": 0.05}      # 表达比例阈值

主链：processed.h5ad X（负值即 scaled → 回退 .raw）按 sender/
receiver 掩码统计每基因表达比例（稀疏行 nnz 口径）→ expr_stats.csv
+ geneset.csv KB 级轻量桥接（矩阵本体不出 Python——大库免疫）→
Rscript /opt/r_tools/nichenetr_bridge.R（Zenodo 7074291 先验四件
构建期烘焙断网可用）→ 读回 ligand_activities/ligand_target_links。

产物：nichenet_ligand_activities.csv（全量降序+rank）/
nichenet_ligand_target_links.csv（top 配体调控边）/
nichenet_ligand_bar.png（top N aupr 条图）/
nichenet_ligand_target_heatmap.png（配体×靶基因权重热图）。
out：n_cells_sender/n_cells_receiver/n_geneset_input/n_geneset_used/
n_background/n_ligands_tested/top_ligands（top10 dict）/n_links。

先验口径三坑（八补记探针代订正 skill v1）：配体名在 $test_ligand
列（tibble）；background 须含配体自身；lt 全零配体列必须剔除。
geneset∩lt rownames <5 或可测配体 <3 → INVALID_INPUT（典型原因：
species 与符号风格不符 / geneset 不在先验靶空间 / sender 无表达）。
"""

from __future__ import annotations

import json
import subprocess
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


def _pct(x: Any, mask: np.ndarray) -> np.ndarray:
    """稀疏/稠密通用：掩码细胞中每基因表达（>0）比例，行=基因。"""
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
    groupby = str(args.get("groupby", ""))
    sender_groups = [str(g) for g in args.get("sender_groups", [])]
    receiver_groups = [str(g) for g in args.get("receiver_groups", [])]
    species = str(args.get("species", ""))
    top_n = int(args.get("top_n_ligands", 20))
    min_expr = float(args.get("min_expr", 0.05))
    ds_dir = WS_ROOT / ds

    if not geneset:
        fail(
            "INVALID_INPUT",
            "geneset is required (receiver-side target "
            "genes, e.g. DE/upregulated genes of the receiver population)",
        )
        return
    if not sender_groups:
        fail(
            "INVALID_INPUT",
            f"sender_groups is required (populations in groupby {groupby!r} that express candidate ligands)",
        )
        return

    p = ds_dir / "processed.h5ad"
    if not p.exists():
        fail(
            "INVALID_INPUT",
            f"no processed.h5ad under dataset {ds!r} — run sc_process first (need obs groups + normalized X)",
        )
        return
    import anndata as ad

    aw = ad.read_h5ad(p)
    x: Any = aw.X
    if x is not None and x.size and float(x.min()) < 0:  # scaled → 回退 .raw
        if aw.raw is not None:
            x = aw.raw.X
        else:
            fail(
                "INVALID_INPUT",
                "processed.h5ad X appears scaled "
                "(negative values) with no .raw fallback — cannot "
                "compute expression percentages",
            )
            return
    genes = pd.Index(aw.var_names.astype(str))
    if aw.raw is not None and x is aw.raw.X:
        genes = pd.Index(aw.raw.var_names.astype(str))

    if groupby not in aw.obs:
        fail(
            "INVALID_INPUT", f"groupby column {groupby!r} not in obs; available: {list(aw.obs.columns)[:20]}"
        )
        return
    cl = aw.obs[groupby].astype(str).to_numpy()
    vals = pd.unique(cl)
    smask = np.isin(cl, sender_groups)
    rmask = np.isin(cl, receiver_groups) if receiver_groups else np.ones(len(cl), dtype=bool)
    if not smask.any():
        fail(
            "INVALID_INPUT",
            f"no cells match sender_groups {sender_groups} in {groupby!r}; values: {list(vals)[:25]}",
        )
        return
    if not rmask.any():
        fail(
            "INVALID_INPUT",
            f"no cells match receiver_groups {receiver_groups} in {groupby!r}; values: {list(vals)[:25]}",
        )
        return

    # 物种：空=自动探测（记忆库六工具惯例）+ 风格矛盾快败
    if not species:
        style = detect_symbol_style(genes)
        species = "mouse" if style == "title" else "human"
    if species not in ("mouse", "human"):
        fail("INVALID_INPUT", f"species must be 'mouse' or 'human' (got {species!r}; empty = auto-detect)")
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
            f"only {used}/{len(geneset)} geneset genes "
            f"exist in the {species} prior target space — check species "
            "(symbol style) or geneset source",
        )
        return
    if tested < 3:
        fail(
            "INVALID_INPUT",
            f"only {tested} ligands testable — sender "
            f"groups {sender_groups} express too few prior ligands "
            "(lower min_expr or check groupby/sender_groups)",
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
    ax.set_title(f"NicheNet ligand activities ({species})", fontsize=9)
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

    top10 = {str(r.test_ligand): round(float(r.aupr_corrected), 4) for r in head.head(10).itertuples()}
    emit(
        {
            "ok": True,
            "species": species,
            "groupby": groupby,
            "n_cells_sender": int(smask.sum()),
            "n_cells_receiver": int(rmask.sum()),
            "n_geneset_input": len(geneset),
            "n_geneset_used": used,
            "n_background": int(meta.get("n_background", 0)),
            "n_ligands_tested": tested,
            "top_ligands": top10,
            "n_links": int(len(links)),
            "ligand_activities_csv": act_csv,
            "ligand_target_links_csv": links_csv,
            "ligand_bar_png": bar_png,
            "ligand_target_heatmap_png": heat_png,
        }
    )


run(main)
