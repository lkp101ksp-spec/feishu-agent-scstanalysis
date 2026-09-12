"""st_misty：多视图空间建模（Phase 49 liana MISTy；Phase 50 PROGENy 视图）。

stdin: {"dataset_id": ..., "n_hvg": 50, "bandwidth": 0, "extra_mode": "hvg"}
intra=deconv.h5ad 细胞型组成（obsm["q05_cell_abundance_w_sf"] 去前缀，
缺失报 ST_MISTY_NO_DECONV）；extra 二选一（extra_mode）：hvg=processed
top HVG 表达（默认，离线安全）；progeny=PROGENy 14 通路活性（decoupler
MLM，模型为构建期快照 /opt/progeny/progeny_human_top500.tsv，运行期
断网）。两者附 obsm["spatial"] 后 genericMistyData 自建 juxta
（n_neighs=6 紧邻）+ para（bandwidth 半径）视图，RandomForestModel
逐目标建模（n_jobs=2 内存纪律）。bandwidth=0 → 5×中位近邻距
（tool-misty l=5 口径）。
产物落 /ws/{ds}/misty/：视图贡献热图 + para 视图 target×predictor
重要性热图 + target_metrics/interactions 两个全量 csv。
容器探针（2026-09-11 liana 1.10.0 实测）：uns["target_metrics"] 列
target/intra_R2/multi_R2/gain_R2/intra/juxta/para（后三=视图贡献）；
uns["interactions"] 列 target/predictor/view/importances，view ∈
{intra,juxta,para}；纯噪声数据 gain_R2=0 甚至可为负（CV R² 性质）。
decoupler 2.2.0 实测：dc.op.progeny(human, top=500)→6463 行 14 通路；
dc.mt.mlm(adata, net, tmin=5) 返回 None，写 obsm["score_mlm"]（n×14
DataFrame）——dc.mlm 不存在，方法在 dc.mt 命名空间。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import (
    WS_ROOT,
    emit,
    ensure_spatial,
    fail,
    load_adata,
    read_args,
    run,
)

ABUND_KEY = "q05_cell_abundance_w_sf"
ABUND_PREFIX = "q05cell_abundance_w_sf_"
VIEW_COLS = ["intra", "juxta", "para"]
PROGENY_TSV = Path("/opt/progeny/progeny_human_top500.tsv")
COLLECTRI_TSV = Path("/opt/collectri/collectri_human.tsv")


def _load_intra(dataset_id: str, obs_names: pd.Index,
                coords: np.ndarray) -> Any:
    """deconv.h5ad 组成矩阵 → intra AnnData（附 obsm['spatial']）。

    intra 行序=abund.loc[common] 顺序（common=obs_names.intersection，
    保持 processed 顺序），coords[pos.isin(common)] 同序对齐。
    """
    import anndata as ad
    p = WS_ROOT / dataset_id / "deconv.h5ad"
    if not p.exists():
        fail("ST_MISTY_NO_DECONV",
             "deconv.h5ad 不存在；先跑 st_deconvolve")
        raise SystemExit(1)
    dec: Any = ad.read_h5ad(p)  # stub 返回 Any | Dataset2D，标 Any 收窄
    if ABUND_KEY not in dec.obsm:
        fail("INVALID_INPUT",
             f"deconv.h5ad 缺 obsm[{ABUND_KEY!r}]（非 st_deconvolve 产物？）")
        raise SystemExit(1)
    abund = dec.obsm[ABUND_KEY]
    if not isinstance(abund, pd.DataFrame):
        abund = pd.DataFrame(np.asarray(abund), index=dec.obs_names)
    abund = abund.copy()
    abund.columns = [str(c).replace(ABUND_PREFIX, "")
                     for c in abund.columns]
    common = obs_names.intersection(abund.index)
    if len(common) < 100:
        fail("INVALID_INPUT",
             f"deconv 与 processed 共有 spot 过少: {len(common)} (<100)")
        raise SystemExit(1)
    comp = abund.loc[common].astype(np.float32)
    pos = pd.Index(obs_names)
    intra = ad.AnnData(X=comp.to_numpy(),
                       var=pd.DataFrame(index=comp.columns))
    intra.obs_names = common
    intra.obsm["spatial"] = coords[pos.isin(common)]
    return intra


def _auto_bandwidth(coords: np.ndarray) -> float:
    """para 半径缺省：5 × 中位最近邻距离（tool-misty l=5 口径）。"""
    from scipy.spatial import cKDTree
    dist, _ = cKDTree(coords).query(coords, k=2)
    return float(np.median(dist[:, 1]) * 5.0)


def _load_progeny_extra(adata: Any, intra: Any,
                        coords: np.ndarray) -> Any:
    """PROGENy MLM 通路活性 → extra AnnData（14 通路列，附 spatial）。

    net=构建期快照 TSV（运行期断网）；dc.mt.mlm 写 obsm['score_mlm']
    （spot×14 DataFrame，列=通路）。net 靶基因∩数据 var_names <100 →
    INVALID_INPUT（基因名非人类 symbol/物种不符）。
    """
    import anndata as ad
    if not PROGENY_TSV.exists():
        fail("ST_MISTY_NO_PROGENY",
             f"{PROGENY_TSV} 缺失（镜像快照层异常，重建 st 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(PROGENY_TSV, sep="\t")
    n_overlap = len(set(net["target"]) & set(adata.var_names))
    if n_overlap < 100:
        fail("INVALID_INPUT",
             f"PROGENy 靶基因与数据交集过少: {n_overlap} (<100，"
             "基因名需为人类 symbol)")
        raise SystemExit(1)
    sub = adata[intra.obs_names, :].copy()
    import decoupler as dc
    dc.mt.mlm(sub, net, tmin=5, verbose=False)
    scores = sub.obsm["score_mlm"].astype(np.float32)
    extra = ad.AnnData(X=scores.to_numpy(),
                       var=pd.DataFrame(index=scores.columns))
    extra.obs_names = intra.obs_names
    extra.obsm["spatial"] = coords[
        adata.obs_names.isin(intra.obs_names)]
    return extra


def _load_collectri_extra(adata: Any, intra: Any,
                          coords: np.ndarray) -> Any:
    """CollecTRI MLM TF 活性 → extra AnnData（~772 TF 列，附 spatial）。

    net=构建期快照 TSV（运行期断网）；dc.mt.mlm 写 obsm['score_mlm']
    （spot×n_TF DataFrame，列=TF，tmin=5 过滤后 ~772）。net 靶基因∩
    数据 var_names <500 → INVALID_INPUT（探针实证：overlap=100 时
    MLM 因邻接矩阵秩 < 协变量数断言失败；真实 Visium 交集数千）。
    """
    import anndata as ad
    if not COLLECTRI_TSV.exists():
        fail("ST_MISTY_NO_COLLECTRI",
             f"{COLLECTRI_TSV} 缺失（镜像快照层异常，重建 st 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(COLLECTRI_TSV, sep="\t")
    n_overlap = len(set(net["target"]) & set(adata.var_names))
    if n_overlap < 500:
        fail("INVALID_INPUT",
             f"CollecTRI 靶基因与数据交集过少: {n_overlap} (<500，"
             "基因名需为人类 symbol)")
        raise SystemExit(1)
    sub = adata[intra.obs_names, :].copy()
    import decoupler as dc
    dc.mt.mlm(sub, net, tmin=5, verbose=False)
    scores = sub.obsm["score_mlm"].astype(np.float32)
    extra = ad.AnnData(X=scores.to_numpy(),
                       var=pd.DataFrame(index=scores.columns))
    extra.obs_names = intra.obs_names
    extra.obsm["spatial"] = coords[
        adata.obs_names.isin(intra.obs_names)]
    return extra


def _contributions_heatmap(tm: pd.DataFrame, png_path: Path) -> None:
    """视图贡献热图：行=target，列=intra/juxta/para 贡献。"""
    import matplotlib.pyplot as plt
    mat = tm.set_index("target")[VIEW_COLS].astype(float)
    fig, ax = plt.subplots(
        figsize=(4.5, max(3.5, mat.shape[0] * 0.35 + 1.5)))
    im = ax.imshow(mat.to_numpy(), aspect="auto", cmap="viridis",
                   vmin=0.0, vmax=1.0)
    ax.set_xticks(range(len(VIEW_COLS)))
    ax.set_xticklabels(VIEW_COLS, fontsize=9)
    ax.set_yticks(range(mat.shape[0]))
    ax.set_yticklabels([str(t) for t in mat.index], fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.7, label="contribution")
    ax.set_title("MISTy view contributions per target", fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _para_interactions_heatmap(inter: pd.DataFrame,
                               png_path: Path,
                               top_n: int = 0) -> pd.DataFrame:
    """para 视图 target×predictor importance 热图。返回透视矩阵。

    top_n>0 且预测子数超出时，只画 importance 总和 top top_n 列
    （tf 模式 ~772 列不可读；csv 仍全量不截断）。
    """
    import matplotlib.pyplot as plt
    para = inter[inter["view"] == "para"]
    mat = para.pivot_table(index="target", columns="predictor",
                           values="importances", fill_value=0.0)
    if top_n > 0 and mat.shape[1] > top_n:
        keep = mat.sum(axis=0).sort_values(
            ascending=False).head(top_n).index
        mat = mat[keep]
    fig, ax = plt.subplots(
        figsize=(max(6.0, mat.shape[1] * 0.35),
                 max(3.5, mat.shape[0] * 0.35 + 1.5)))
    im = ax.imshow(mat.to_numpy(), aspect="auto", cmap="magma")
    ax.set_xticks(range(mat.shape[1]))
    ax.set_xticklabels([str(c) for c in mat.columns], rotation=90,
                       fontsize=6)
    ax.set_yticks(range(mat.shape[0]))
    ax.set_yticklabels([str(t) for t in mat.index], fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.7, label="importance (para)")
    ax.set_title("MISTy paracrine interactions (target × predictor)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return mat


def main() -> None:
    """主流程：intra/extra 视图 → MISTy 建模 → 热图/csv → emit。"""
    args = read_args()
    n_hvg = int(args.get("n_hvg", 50))
    bandwidth = float(args.get("bandwidth", 0) or 0)
    if n_hvg < 10 or n_hvg > 500:
        fail("INVALID_INPUT", f"n_hvg={n_hvg} 越界（需 10..500）")
        raise SystemExit(1)
    if bandwidth < 0:
        fail("INVALID_INPUT", f"bandwidth={bandwidth} 不能为负")
        raise SystemExit(1)
    extra_mode = str(args.get("extra_mode", "hvg"))
    if extra_mode not in ("hvg", "progeny"):
        fail("INVALID_INPUT",
             f"extra_mode={extra_mode!r} 非法（需 hvg|progeny）")
        raise SystemExit(1)

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    coords = np.asarray(adata.obsm["spatial"], dtype=np.float64)
    intra = _load_intra(args["dataset_id"], adata.obs_names, coords)

    # extra 二选一：hvg=top HVG 表达（var 有 highly_variable 用之，否则
    # 按方差取）；progeny=PROGENy 通路活性。均与 intra 同 spot 顺序对齐
    # （intra.obs_names 即 common 交集顺序）
    import anndata as ad
    if extra_mode == "progeny":
        extra = _load_progeny_extra(adata, intra, coords)
        n_pred = int(extra.n_vars)
    elif extra_mode == "tf":
        extra = _load_collectri_extra(adata, intra, coords)
        n_pred = int(extra.n_vars)
    else:
        hvgs: list[str]
        if "highly_variable" in adata.var.columns:
            hvgs = adata.var.index[
                adata.var["highly_variable"]].tolist()[:n_hvg]
        else:
            x = adata.X
            x_var = np.asarray(x.var(axis=0)).ravel()
            hvgs = adata.var.index[
                np.argsort(x_var)[::-1][:n_hvg]].tolist()
        if len(hvgs) < 10:
            fail("INVALID_INPUT", f"可用 HVG 过少（{len(hvgs)} < 10）")
            raise SystemExit(1)
        sub = adata[intra.obs_names, hvgs]
        extra = ad.AnnData(
            X=np.asarray(
                sub.X.todense() if hasattr(sub.X, "todense") else sub.X,
                dtype=np.float32),
            var=pd.DataFrame(index=hvgs))
        extra.obs_names = intra.obs_names
        extra.obsm["spatial"] = coords[
            adata.obs_names.isin(intra.obs_names)]
        n_pred = len(hvgs)

    bw = bandwidth if bandwidth > 0 else _auto_bandwidth(
        extra.obsm["spatial"])

    from liana.method import genericMistyData
    from liana.method.sp import RandomForestModel
    misty = genericMistyData(intra=intra, extra=extra, cutoff=0.05,
                             bandwidth=bw, n_neighs=6, verbose=False)
    misty(model=RandomForestModel, n_jobs=2, verbose=False, seed=42)
    tm = misty.uns["target_metrics"]
    inter = misty.uns["interactions"]

    out_dir = WS_ROOT / args["dataset_id"] / "misty"
    out_dir.mkdir(parents=True, exist_ok=True)
    tm_csv = out_dir / "misty_target_metrics.csv"
    tm.to_csv(tm_csv, index=False)
    inter_csv = out_dir / "misty_interactions.csv"
    inter.to_csv(inter_csv, index=False)
    contrib_png = out_dir / "misty_contributions.png"
    _contributions_heatmap(tm, contrib_png)
    para_png = out_dir / "misty_interactions_para.png"
    _para_interactions_heatmap(inter, para_png)

    # 每 target para 视图 top1 预测子（importance 最大）
    para = inter[inter["view"] == "para"]
    top: dict[str, dict[str, Any]] = {}
    for target, grp in para.groupby("target"):
        row = grp.loc[grp["importances"].idxmax()]
        top[str(target)] = {"predictor": str(row["predictor"]),
                            "importance": round(float(row["importances"]), 4)}

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "extra_mode": extra_mode,
        "n_targets": int(tm.shape[0]),
        "n_predictors": n_pred,
        "n_predictors_total": n_pred,
        "n_predictors_shown": min(top_n, n_pred) if top_n else n_pred,
        "n_spots": int(intra.n_obs),
        "bandwidth": round(bw, 2),
        "mean_gain_R2": round(float(tm["gain_R2"].mean()), 4),
        "top_interactions": top,
        # pngs 聚合键：IM 发图与 D 报告共用宿主四键收集
        "pngs": [str(contrib_png), str(para_png)],
        "contributions_png": str(contrib_png),
        "interactions_para_png": str(para_png),
        "target_metrics_csv": str(tm_csv),
        "interactions_csv": str(inter_csv),
        "note": "liana MISTy（genericMistyData intra/juxta/para + "
                "RandomForestModel）；intra=细胞型组成，extra="
                "top HVG 表达或 PROGENy 通路活性或 CollecTRI TF 活性"
                "（extra_mode）；importance=Gini 下降",
    })


if __name__ == "__main__":
    run(main)
