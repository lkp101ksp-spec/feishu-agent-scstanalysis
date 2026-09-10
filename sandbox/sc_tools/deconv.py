"""sc_deconv：bulk 解卷积（Phase 34，对齐 toolsv1 server_bulk_deconvolution）。

stdin: {"dataset_id": ..., "celltype_col": "leiden",
        "bulk_path": "<rel-under-/data>", "method": "wnnls", "top_n": 200}
bulk 矩阵为 /data 挂载下 csv/tsv（行=基因、列=样本；若转置方向与 sc
参考基因交集更大则自动转置并在 transpose_note 说明）。
method=wnnls：MuSiC 式加权 NNLS（权重=1/sqrt(参考内基因跨细胞方差)）；
method=nusvr：CIBERSORT 式线性 NuSVR（简化版，无 nu 调参特征选择）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import DATA_ROOT, WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata: Any) -> str:
    """列出可作细胞标签的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def main() -> None:
    """主流程：signature 构建 → 逐样本解卷积 → 比例 csv + 堆叠柱 + 热图。"""
    import matplotlib.pyplot as plt
    from scipy.optimize import nnls
    from scipy.sparse import issparse

    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    bulk_path = str(args.get("bulk_path", "")).strip()
    method = str(args.get("method", "wnnls")).strip().lower()
    top_n = int(args.get("top_n", 200))
    if not bulk_path:
        raise ValueError("bulk_path is required (relative to data root)")
    if method not in ("wnnls", "nusvr"):
        raise ValueError(f"method must be wnnls or nusvr, got {method!r}")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        raise ValueError(
            f"celltype column {celltype_col!r} not in obs; available: "
            f"{_cat_cols(adata)}")

    # sc 参考 → CPM 线性空间 signature（基因×类型均值）
    raw = adata.raw.to_adata() if adata.raw is not None else adata
    X = raw.X.toarray() if issparse(raw.X) else np.asarray(raw.X)
    lib = X.sum(axis=1).astype(float)
    lib[lib == 0] = 1.0
    cpm = X / lib[:, None] * 1e4
    labels = raw.obs[celltype_col].astype(str).to_numpy()
    types = sorted(pd.unique(labels).tolist())
    gene_var = pd.Series(cpm.var(axis=0), index=raw.var_names.astype(str))
    S = pd.DataFrame(
        {t: cpm[labels == t].mean(axis=0) for t in types},
        index=raw.var_names.astype(str))

    # bulk 读入 + 方向自纠（样本在行时转置）
    p = (DATA_ROOT / bulk_path).resolve()
    if DATA_ROOT not in p.parents:
        raise ValueError(f"invalid bulk_path: {bulk_path!r}")
    bulk = pd.read_csv(p, sep=None, engine="python", index_col=0)
    bulk.index = bulk.index.astype(str)
    bulk.columns = bulk.columns.astype(str)
    transpose_note = ""
    if (len(set(bulk.columns) & set(S.index))
            > len(set(bulk.index) & set(S.index))):
        bulk = bulk.T
        transpose_note = "bulk matrix auto-transposed (samples were in rows)"
    genes = sorted(set(bulk.index) & set(S.index))
    if len(genes) < 50:
        raise ValueError(
            f"only {len(genes)} genes overlap between bulk and sc reference; "
            "check gene ID consistency (symbol vs ensembl)")
    bulk = bulk.loc[genes].astype(float)
    S = S.loc[genes]

    # signature 基因选择：类型间方差 top_n
    sel = S.var(axis=1).nlargest(min(top_n, len(genes))).index
    S_sel = S.loc[sel]

    # 逐样本解卷积（wnnls 权重=1/sqrt(参考内基因跨细胞方差)）
    w = 1.0 / np.sqrt(gene_var.loc[sel].to_numpy() + 1e-6)
    props = {}
    for sample in bulk.columns:
        y = np.maximum(bulk.loc[sel, sample].to_numpy(dtype=float), 0.0)
        if method == "wnnls":
            coef, _ = nnls(S_sel.to_numpy() * w[:, None], y * w)
        else:
            from sklearn.svm import NuSVR
            fit = NuSVR(nu=0.5, kernel="linear").fit(
                S_sel.to_numpy().T, y)
            coef = (fit.dual_coef_ @ fit.support_vectors_).ravel()
        coef = np.maximum(np.asarray(coef, dtype=float), 0.0)
        total = coef.sum()
        props[sample] = (coef / total if total > 0
                         else np.full(len(types), 1.0 / len(types)))
    P = pd.DataFrame(props, index=types)  # 类型 × 样本

    out_dir = WS_ROOT / args["dataset_id"] / "deconv"
    out_dir.mkdir(parents=True, exist_ok=True)
    prop_csv = out_dir / f"deconv_proportions_{method}.csv"
    P.T.to_csv(prop_csv, index_label="sample")
    sig_csv = out_dir / "deconv_signature.csv"
    S_sel.to_csv(sig_csv, index_label="gene")

    # 堆叠柱状图（样本×类型比例）
    fig, ax = plt.subplots(figsize=(max(4, 0.6 * P.shape[1] + 2), 4.5))
    bottom = np.zeros(P.shape[1])
    cmap = plt.get_cmap("tab20")
    for ti, t in enumerate(P.index):
        vals = P.loc[t].to_numpy()
        ax.bar(P.columns, vals, bottom=bottom, label=t,
               color=cmap(ti % 20), width=0.8)
        bottom += vals
    ax.set_ylabel("proportion")
    ax.set_title(f"bulk deconvolution ({method})", fontsize=10)
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.0, 0.5))
    ax.tick_params(axis="x", rotation=45, labelsize=7)
    for lbl in ax.get_xticklabels():
        lbl.set_horizontalalignment("right")
    fig.tight_layout()
    bar_png = out_dir / f"deconv_barplot_{method}.png"
    fig.savefig(bar_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 热图（类型×样本比例）
    fig, ax = plt.subplots(figsize=(max(4, 0.6 * P.shape[1] + 2),
                                    max(3, 0.5 * len(types) + 2)))
    im = ax.imshow(P.to_numpy(), cmap="viridis", aspect="auto",
                   vmin=0, vmax=1)
    ax.set_xticks(range(P.shape[1]))
    ax.set_xticklabels(P.columns, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(types)))
    ax.set_yticklabels(P.index, fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.8, label="proportion")
    ax.set_title(f"cell type proportions ({method})", fontsize=10)
    fig.tight_layout()
    heat_png = out_dir / f"deconv_heatmap_{method}.png"
    fig.savefig(heat_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    dominant = [
        {"sample": str(c),
         "dominant_type": str(P[c].idxmax()),
         "proportion": round(float(P[c].max()), 3)}
        for c in P.columns[:20]]

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        "n_samples": int(P.shape[1]),
        "n_celltypes": len(types),
        "n_genes_used": int(len(sel)),
        "dominant": dominant,
        "proportions_csv": str(prop_csv),
        "signature_csv": str(sig_csv),
        "barplot_png": str(bar_png),
        "heatmap_png": str(heat_png),
        "transpose_note": transpose_note,
        "method_note": ("wnnls=MuSiC 式加权 NNLS；nusvr=CIBERSORT 式线性 "
                        "NuSVR 简化版（无 nu 调参与逐步特征选择）"),
    })


if __name__ == "__main__":
    run(main)
