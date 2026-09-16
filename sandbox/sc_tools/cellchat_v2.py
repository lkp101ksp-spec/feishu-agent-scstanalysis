"""sc_cellchat_v2 / st_cellchat_v2：R 版 CellChat 2.2.0.9001 通讯分析
（Phase 57；jinworks fork GitHub 源装，bio 镜像单点安装，st 入口由
l3_spatial 跨镜像分发到 bio 容器——同一 WS_ROOT 卷直读 st processed.h5ad）。

stdin: {"dataset_id": ..., "celltype_col": "leiden", "species": "human",
        "min_cells": 10, "max_cells_per_group": 0, "top_n": 30,
        "interaction_range": 250.0}
- 单细胞模式（默认）：processed.h5ad（sc_process 产物，含 raw）
- 空间模式（自动）：obsm["spatial"] 存在时启用（st processed.h5ad），
  distance.use 约束 + Cell-Cell Contact 接触依赖（contact.knn.k=6）
- CellChatDB v2（3233 互作）随包离线内置，容器断网可用
- 与 sc_cellchat（liana 复现）互补：通路级聚合 + 11 种网络中心性
  （hub/authority/eigen/page_rank/flowbet/info...）是 v2 独有能力面

桥接（r_tools/cellchat2_bridge.R，mtx+CSV 双向）：
Python 导出 expr.mtx(genes×cells)+meta.csv(+spatial.csv) → R 跑
createCellChat→subsetData→identifyOverExpressed*→computeCommunProb
(truncatedMean trim=0.1, nboot=100)→filterCommunication→
computeCommunProbPathway→aggregateNet→netAnalysis_computeCentrality →
lr/pathway/centrality/counts/weights CSV → Python 读回画图 emit。

v2.2 API 坑位钉注（镜像内 args()/源码实测，详见测试总结第五十六段）：
- identifyOverExpressedGenes 需 presto 或 do.fast=FALSE（桥内走后者）
- 矩阵输入不填 @data.raw 且 raw.use=FALSE 路径坏——桥内补
  cc@data.raw 走默认 TRUE（数学等价 v1 normalized 口径）
- LRuse→LRsig、centrality→centr（槽位改名）
- spatial.factors 必须含 ratio（坐标→µm）+tol；contact.knn.k 必须显式
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run

R_SCRIPT = Path("/opt/r_tools/cellchat2_bridge.R")
SIG_THRESH = 0.05


def _cat_cols(adata: Any) -> str:
    """列出可作细胞标签的 obs 列（2..50 取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _spot_um_scale(adata: Any) -> tuple[float, str]:
    """visium fullres 像素 → µm 缩放（55µm 标称直径 / spot_diameter_fullres）。

    取不到 scalefactors 时返回 1.0 并给提示文案（坐标按原单位口径）。
    """
    try:
        sp = adata.uns.get("spatial", {})
        for lib in sp.values():
            sf = (lib or {}).get("scalefactors", {})
            dia = sf.get("spot_diameter_fullres")
            if dia and float(dia) > 0:
                return 55.0 / float(dia), ""
    except Exception:
        pass
    return 1.0, ("spatial scalefactors 缺失：坐标按原单位口径传入 "
                 "（ratio=1），interaction_range 需与坐标同单位")


def _prepare(adata: Any, celltype_col: str, min_cells: int,
             max_cells_per_group: int) -> tuple[Any, list[str], bool, int]:
    """小群剔除 + 可选分层抽样 → (adata, dropped, subsampled, n_total)。"""
    labels = adata.obs[celltype_col].astype(str)
    dropped = labels.value_counts().loc[lambda s: s < min_cells].index.tolist()
    if dropped:
        adata = adata[~labels.isin(dropped)].copy()
        labels = adata.obs[celltype_col].astype(str)
    if labels.nunique() < 2:
        raise ValueError(
            f"need >=2 cell types with >= {min_cells} cells each; "
            f"dropped too-small: {dropped}")
    n_total = adata.n_obs
    if max_cells_per_group > 0:
        idx: list[Any] = []
        for _, g in adata.obs.groupby(celltype_col):
            idx.extend(g.sample(n=min(max_cells_per_group, len(g)),
                                random_state=42).index)
        adata = adata[idx].copy()
    return adata, dropped, adata.n_obs < n_total, n_total


def _export_bridge_inputs(mat: Any, adata: Any, labels: pd.Series,
                          work: Path, spatial: bool,
                          uscale: float) -> tuple[Path, dict[str, str]]:
    """导出 R 桥输入（genes×cells mtx + meta + 可选坐标）。

    CellChat 拒绝纯数字标签（setIdent: labels cannot contain `0`，
    59900 leiden 真机曝缺）——任一标签以数字开头时全体加 "C" 前缀，
    返回 (work, 前缀→原名映射表) 供读回还原。
    """
    import scipy.io as sio

    lab = labels.astype(str)
    needs_prefix = lab.str.match(r"^\d").any()
    if needs_prefix:
        lab = "C" + lab
    rename_map = dict(zip(lab.unique(), labels.astype(str).unique()))
    work.mkdir(parents=True, exist_ok=True)
    import scipy.sparse as sp

    x = mat.X
    coo = (x.T.tocoo() if sp.issparse(x)
           else sp.coo_matrix(np.asarray(x).T))  # genes × cells
    sio.mmwrite(work / "expr.mtx", coo, field="real")
    (work / "features.tsv").write_text(
        "\n".join(map(str, mat.var_names)) + "\n", encoding="utf-8")
    (work / "barcodes.tsv").write_text(
        "\n".join(map(str, mat.obs_names)) + "\n", encoding="utf-8")
    meta = pd.DataFrame({"cell": mat.obs_names.astype(str),
                         "type": lab.values})
    meta.to_csv(work / "meta.csv", index=False)
    if spatial:
        coords = np.asarray(adata[mat.obs_names].obsm["spatial"])[:, :2]
        pd.DataFrame({"cell": mat.obs_names.astype(str),
                      "x": coords[:, 0], "y": coords[:, 1]}
                     ).to_csv(work / "spatial.csv", index=False)
    del uscale  # uscale 经 argv 传桥，不落文件
    return work, rename_map if needs_prefix else {}


def _plots(lr: pd.DataFrame, pw: pd.DataFrame, cen: pd.DataFrame,
           counts: pd.DataFrame, top_n: int, mode: str,
           out_dir: Path) -> tuple[Path, Path, Path]:
    """三图：top LR dotplot、显著互作计数热图、hub 中心性热图。"""
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    lr = lr.sort_values(["prob"], ascending=False).reset_index(drop=True)
    # dotplot：行=top LR（interaction_name），列=top source→target
    top_lrs = lr["interaction_name"].drop_duplicates().head(top_n).tolist()
    sig = lr[lr["pval"] < SIG_THRESH]
    top_pairs = (sig.assign(pair=sig["source"] + "->" + sig["target"])
                 ["pair"].value_counts().head(12).index.tolist()
                 or lr["source"].astype(str).add("->" + lr["target"].astype(str))
                 .drop_duplicates().head(12).tolist())
    sub = lr.assign(pair=lr["source"].astype(str) + "->"
                    + lr["target"].astype(str))
    sub = sub[sub["interaction_name"].isin(top_lrs) & sub["pair"]
              .isin(top_pairs)]
    fig, ax = plt.subplots(figsize=(max(4, 0.9 * len(top_pairs) + 2),
                                    max(4, 0.32 * len(top_lrs) + 2)))
    for yi, name in enumerate(top_lrs):
        for xi, pair in enumerate(top_pairs):
            hit = sub[(sub["interaction_name"] == name)
                      & (sub["pair"] == pair)]
            if hit.empty:
                continue
            r = hit.iloc[0]
            ax.scatter(xi, yi,
                       s=20 + 120 * -np.log10(max(float(r["pval"]), 1e-12))
                       / 12, c=[float(r["prob"])], cmap="viridis", vmin=0,
                       vmax=float(lr["prob"].max()) or 1.0, linewidths=0)
    ax.set_xticks(range(len(top_pairs)))
    ax.set_xticklabels(top_pairs, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(top_lrs)))
    ax.set_yticklabels(top_lrs, fontsize=7)
    ax.set_title(f"top LR interactions (CellChat v2, {mode})", fontsize=10)
    fig.tight_layout()
    dot_png = out_dir / "cellchat2_dotplot.png"
    fig.savefig(dot_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 计数热图：source × target 显著互作数
    types = list(counts.index)
    fig, ax = plt.subplots(figsize=(max(4, 0.7 * len(types) + 2),
                                    max(3, 0.6 * len(types) + 2)))
    im = ax.imshow(counts.to_numpy(dtype=float), cmap="Reds")
    ax.set_xticks(range(len(types)), types, rotation=45, ha="right",
                  fontsize=8)
    ax.set_yticks(range(len(types)), types, fontsize=8)
    ax.set_xlabel("target")
    ax.set_ylabel("source")
    ax.set_title(f"significant interaction counts ({mode})", fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    heat_png = out_dir / "cellchat2_heatmap.png"
    fig.savefig(heat_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # hub 中心性热图：通路 × 细胞型（hub=信号发送主导度）
    hub_png = dot_png
    if len(cen):
        hub = cen[cen["measure"] == "hub"]
        if len(hub):
            piv = hub.pivot_table(index="pathway", columns="celltype",
                                  values="value", aggfunc="mean")
            piv = piv.loc[piv.max(axis=1).sort_values(ascending=False)
                          .index].head(15)
            fig, ax = plt.subplots(figsize=(max(4, 0.8 * piv.shape[1] + 3),
                                            max(3, 0.4 * len(piv) + 2)))
            im = ax.imshow(piv.to_numpy(), cmap="YlOrRd", aspect="auto")
            ax.set_xticks(range(piv.shape[1]), piv.columns, rotation=45,
                          ha="right", fontsize=8)
            ax.set_yticks(range(len(piv)), piv.index, fontsize=7)
            ax.set_title("hub centrality by pathway (sender dominance)",
                         fontsize=10)
            fig.colorbar(im, ax=ax, shrink=0.8)
            fig.tight_layout()
            hub_png = out_dir / "cellchat2_hub.png"
            fig.savefig(hub_png, dpi=150, bbox_inches="tight")
            plt.close(fig)
    return dot_png, heat_png, hub_png


def main() -> None:
    """主流程：数据准备 → 桥输入导出 → Rscript → 读回画图 emit。"""
    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    species = str(args.get("species", "human")).strip().lower()
    min_cells = int(args.get("min_cells", 10))
    top_n = int(args.get("top_n", 30))
    max_cells_per_group = int(args.get("max_cells_per_group", 0))
    interaction_range = float(args.get("interaction_range", 250.0))
    if species not in ("human", "mouse"):
        fail("INVALID_INPUT",
             f"species must be human or mouse, got {species!r}")
        return

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        fail("INVALID_INPUT",
             f"celltype column {celltype_col!r} not in obs; available: "
             f"{_cat_cols(adata)}")
        return
    spatial = "spatial" in adata.obsm

    # 资源分级护栏（与 sc_cellchat 同规）：超大数据集未显式设上限时
    # 自动分层抽样，emit 钉注 auto_capped
    auto_capped = max_cells_per_group <= 0 and adata.n_obs > 80000
    if auto_capped:
        max_cells_per_group = 5000

    adata, dropped, subsampled, n_total = _prepare(
        adata, celltype_col, min_cells, max_cells_per_group)
    labels = adata.obs[celltype_col].astype(str)

    # 表达源：raw 快照（normalized）优先，其次 X（负值即 scale 数据拒收）
    mat = adata.raw.to_adata() if adata.raw is not None else adata
    if mat is adata:
        import scipy.sparse as sp

        x = mat.X
        dense_probe = x[: min(50, x.shape[0])]
        if sp.issparse(x):
            dense_probe = dense_probe.toarray()
        if np.any(np.asarray(dense_probe) < 0):
            fail("ST_STATE_INVALID" if spatial else "SC_STATE_INVALID",
                 "processed.h5ad has no raw snapshot and X contains "
                 "negative (scaled) values; rerun process pipeline first")
            return

    uscale, scale_note = (1.0, "")
    if spatial:
        uscale, scale_note = _spot_um_scale(adata)

    ds_dir = WS_ROOT / args["dataset_id"]
    work = ds_dir / "cellchat_v2_bridge"
    out_dir = ds_dir / "cellchat_v2"
    _, rename_map = _export_bridge_inputs(
        mat, adata, labels, work, spatial, uscale)

    cmd = ["Rscript", str(R_SCRIPT), str(work), str(out_dir), species,
           str(min_cells), "1" if spatial else "0",
           str(interaction_range), str(uscale)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0 or "CELLCHAT2_DONE" not in proc.stdout:
        fail("CELLCHAT2_BRIDGE_FAIL",
             f"Rscript cellchat2_bridge.R failed: {proc.stderr[-1500:]}")
        return

    lr = pd.read_csv(out_dir / "lr.csv")
    pw = pd.read_csv(out_dir / "pathway.csv") if (
        out_dir / "pathway.csv").exists() else pd.DataFrame()
    cen = pd.read_csv(out_dir / "centrality.csv") if (
        out_dir / "centrality.csv").exists() else pd.DataFrame()
    counts = pd.read_csv(out_dir / "counts.csv", index_col=0)
    # 数字标签前缀还原（映射表精确替换，不误伤原生 C 开头标签）
    if rename_map:
        for df in (lr, pw, cen):
            for col in ("source", "target", "celltype"):
                if col in df.columns:
                    df[col] = df[col].astype(str).map(
                        lambda v: rename_map.get(v, v))
        counts.rename(index=rename_map, columns=rename_map, inplace=True)
        lr.to_csv(out_dir / "lr.csv", index=False)
        if len(pw):
            pw.to_csv(out_dir / "pathway.csv", index=False)
        if len(cen):
            cen.to_csv(out_dir / "centrality.csv", index=False)
        counts.to_csv(out_dir / "counts.csv")
    dot_png, heat_png, hub_png = _plots(
        lr, pw, cen, counts, top_n,
        "spatial" if spatial else "single", out_dir)

    sig = lr[lr["pval"] < SIG_THRESH]
    top = [
        {"interaction": str(r["interaction_name"]),
         "pathway": str(r["pathway_name"]),
         "source": str(r["source"]), "target": str(r["target"]),
         "prob": round(float(r["prob"]), 3),
         "pval": float(f"{r['pval']:.2e}")}
        for _, r in lr.head(top_n).iterrows()]
    top_pw = [
        {"pathway": str(r["pathway_name"]),
         "source": str(r["source"]), "target": str(r["target"]),
         "prob": round(float(r["prob"]), 3)}
        for _, r in (pw.sort_values("prob", ascending=False)
                     .head(top_n).iterrows())] if len(pw) else []
    payload: dict[str, Any] = {
        "ok": True, "dataset_ref": args["dataset_id"],
        "mode": "spatial" if spatial else "single",
        "celltype_col": celltype_col, "species": species,
        "n_celltypes": int(labels.nunique()),
        "n_cells_used": int(mat.n_obs), "n_lr": int(len(lr)),
        "n_sig": int(len(sig)), "n_pathway": int(len(pw)),
        "top": top, "top_pathway": top_pw,
        "dropped_small_types": [str(d) for d in dropped],
        "subsampled": subsampled, "auto_capped": auto_capped,
        "lr_csv": str(out_dir / "lr.csv"),
        "pathway_csv": str(out_dir / "pathway.csv") if len(pw) else "",
        "centrality_csv": str(out_dir / "centrality.csv") if len(cen) else "",
        "counts_csv": str(out_dir / "counts.csv"),
        "dotplot_png": str(dot_png), "heatmap_png": str(heat_png),
        "hub_png": str(hub_png),
        "interaction_range": interaction_range,
    }
    if spatial:
        payload["unit_scale"] = uscale
    if scale_note:
        payload["note"] = scale_note
    if subsampled:
        payload["note"] = payload.get("note", "") + (
            f"按 {celltype_col} 分层抽样：每组最多 {max_cells_per_group} "
            f"细胞（{n_total}→{mat.n_obs}），结果为抽样估计"
            + ("；超大数据集自动触发" if auto_capped else ""))
    emit(payload)


if __name__ == "__main__":
    run(main)
