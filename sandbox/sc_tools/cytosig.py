"""sc_cytosig：细胞因子信号预测（Phase 71，L3 工具，CytoSig 桥）。

stdin 契约（dataset_ref/groupby 必填）：
  {"dataset_ref": "abc123", "groupby": "celltype", "mode": "diff",
   "nrand": 1000}

数据流（spec 2026-09-19-sc-cytosig-design.md §2）：processed.h5ad →
表达矩阵（raw 快照优先，log 全基因；var 与 4881 基因签名 upper 对齐，
交集 <500 拒收）→ mode=diff 逐群差分谱（群均值-其余均值，论文口径）/
mode=per_cell 逐细胞矩阵 → tsv 落盘 → subprocess 调
/opt/cytosig_env/bin/python _cytosig_core.py（numpy<2 venv，ridge +
置换）→ 读回 beta/std/zscore/pvalue → 群汇总（per_cell）+ 三产物。

口径钉注：beta=信号强度（CytoSig 网站排名口径）、zscore=置换显著性
——两者排名可能不同，不可混用（产物/摘要双列并列）。

产物（落 /ws/{ref}/cytosig/）：cytosig_scores.csv / 热图 / top 条形。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run

VENV_PY = "/opt/cytosig_env/bin/python"
CORE = "/opt/sc_tools/_cytosig_core.py"
MIN_OVERLAP = 500


def _die(code: str, msg: str) -> None:
    """结构化失败：common.fail 输出 JSON 后退出（错误码见 spec §7）。"""
    fail(code, msg)
    raise SystemExit(1)


def _signature_genes() -> pd.Index:
    """venv 内签名基因列表（主环境无 CytoSig 包，借 venv 解释器
    一行取 find_signature_path，路径随版本自动对齐）。"""
    proc = subprocess.run(
        [VENV_PY, "-c", "import CytoSig; print(CytoSig.find_signature_path())"],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        _die("CYTOSIG_CORE_FAILED",
             f"venv signature path probe rc={proc.returncode}: "
             f"{proc.stderr[-300:]}")
    sig_path = proc.stdout.strip().splitlines()[-1]
    return pd.read_csv(sig_path, sep="\t", index_col=0).index


def _expr_frame(adata: Any) -> pd.DataFrame:
    """表达矩阵：raw 快照优先（log 全基因），否则 X；upper 对齐签名。

    签名基因表直接读 venv 内文件（主环境无 CytoSig 包，文件是纯 tsv）。
    """
    src = adata.raw.to_adata() if adata.raw is not None else adata
    X = np.asarray(src.X.todense()) if hasattr(src.X, "todense") \
        else np.asarray(src.X)
    df = pd.DataFrame(X.T, index=src.var_names.astype(str),
                      columns=[str(c) for c in src.obs_names])
    # 签名基因大写 ↔ var 原名（小鼠 Titlecase 也被 upper 覆盖）；一对多撞名去重保首个
    upper_map: dict[str, str] = {}
    for v in df.index:
        upper_map.setdefault(v.upper(), v)
    sig_genes = _signature_genes()
    # 签名基因也 upper 化再对齐：签名含 C10orf88 类混合大小写符号
    # （HGNC 旧式写法），双侧 upper 才能全量命中（探针冒烟 770/800 教训）
    keep = [upper_map[g] for g in sig_genes.str.upper() if g in upper_map]
    df = df.loc[keep]
    df.index = [i.upper() for i in df.index]  # 内核按大写签名交集
    return df[~df.index.duplicated(keep="first")]


def _diff_matrix(expr: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    """逐群差分谱：群均值 - 其余均值（基因×群，CytoSig README 口径）。"""
    cols = {}
    for g in sorted(groups.unique()):
        m = (groups == g).to_numpy()
        cols[str(g)] = expr.iloc[:, m].mean(axis=1) - \
            expr.iloc[:, ~m].mean(axis=1)
    return pd.DataFrame(cols)


def _plot(scores: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """热图（top20 因子 × 样本，按最大 beta 排序）+ 逐样本 top5 条形。"""
    import matplotlib.pyplot as plt

    beta = (scores[scores["kind"] == "beta"]
            .pivot(index="factor", columns="sample", values="value"))
    top_factors = beta.max(axis=1).sort_values(ascending=False).head(20)
    hm = beta.loc[top_factors.index]
    fig, ax = plt.subplots(figsize=(max(6, 0.35 * hm.shape[1] + 3),
                                    max(5, 0.3 * len(hm) + 2)), dpi=150)
    im = ax.imshow(hm.to_numpy(), aspect="auto", cmap="RdBu_r")
    ax.set_xticks(range(hm.shape[1]))
    ax.set_xticklabels(hm.columns, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(hm.shape[0]))
    ax.set_yticklabels(hm.index, fontsize=7)
    fig.colorbar(im, ax=ax, label="beta (signal strength)", shrink=0.8)
    ax.set_title("CytoSig cytokine signaling (top 20 factors by beta)",
                 fontsize=10)
    fig.tight_layout()
    hm_png = out_dir / "cytosig_heatmap.png"
    fig.savefig(hm_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    zs = (scores[scores["kind"] == "zscore"]
          .pivot(index="factor", columns="sample", values="value"))
    n_s = min(6, beta.shape[1])
    samples = beta.max(axis=0).sort_values(ascending=False).head(n_s).index
    fig, axes = plt.subplots(1, n_s, figsize=(3.2 * n_s, 4.5), dpi=150)
    axes = np.atleast_1d(axes)
    for ax, s in zip(axes, samples):
        top = beta[s].sort_values(ascending=False).head(5)
        y = np.arange(len(top))[::-1]
        ax.barh(y + 0.18, top.to_numpy(), height=0.36, label="beta",
                color="steelblue")
        zvals = zs.loc[top.index, s] if s in zs.columns else pd.Series(
            [np.nan] * len(top), index=top.index)
        ax.barh(y - 0.18, zvals.to_numpy(), height=0.36, label="zscore",
                color="darkorange")
        ax.set_yticks(y)
        ax.set_yticklabels(top.index, fontsize=7)
        ax.set_title(str(s), fontsize=8)
        ax.legend(fontsize=6)
    fig.suptitle("Top 5 factors per sample (beta vs zscore)", fontsize=10)
    fig.tight_layout()
    top_png = out_dir / "cytosig_top.png"
    fig.savefig(top_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return hm_png, top_png


def main() -> None:
    """主流程：读参 → 表达矩阵 → mode 分支 → venv ridge → 产物。"""
    args = read_args()
    dataset_ref = str(args["dataset_ref"])
    groupby = str(args["groupby"]).strip()
    mode = str(args.get("mode", "diff")).strip().lower()
    nrand = int(args.get("nrand", 1000))
    if mode not in ("diff", "per_cell"):
        _die("INVALID_INPUT", f"unknown mode {mode!r}; "
             "expected 'diff' or 'per_cell'")
    if not 100 <= nrand <= 5000:
        _die("INVALID_INPUT", f"nrand {nrand} out of range 100..5000")
    if not Path(VENV_PY).exists():
        _die("CYTOSIG_VENV_MISSING",
             f"{VENV_PY} not found — rebuild bio image with the "
             "Phase 71 cytosig venv layer")

    adata = load_adata({"dataset_id": dataset_ref, "file": "processed"})
    if groupby not in adata.obs:
        cols = [c for c in adata.obs.columns
                if 2 <= adata.obs[c].astype(str).nunique() <= 50]
        _die("INVALID_INPUT",
             f"groupby {groupby!r} not in obs; candidate cols: "
             f"{cols[:10]}")
    groups = adata.obs[groupby].astype(str)
    if groups.nunique() < 2:
        _die("INVALID_INPUT",
             f"groupby {groupby!r} has single value — nothing to compare")

    expr = _expr_frame(adata)
    if expr.shape[0] < MIN_OVERLAP:
        _die("CYTOSIG_LOW_OVERLAP",
             f"signature gene overlap {expr.shape[0]} < {MIN_OVERLAP} — "
             "expression matrix likely wrong species/identifier scheme")

    if mode == "diff":
        Y = _diff_matrix(expr, groups)
    else:
        Y = expr  # 基因×细胞（per_cell）

    out_dir = WS_ROOT / dataset_ref / "cytosig"
    out_dir.mkdir(parents=True, exist_ok=True)
    expr_tsv = out_dir / "_expr_input.tsv"
    res_tsv = out_dir / "_core_result.tsv"
    Y.to_csv(expr_tsv, sep="\t")

    proc = subprocess.run(
        [VENV_PY, CORE, str(expr_tsv), str(res_tsv), str(nrand)],
        capture_output=True, text=True, timeout=1500)
    if proc.returncode == 3:
        _die("CYTOSIG_LOW_OVERLAP", "venv core reported overlap <50")
    if proc.returncode != 0:
        _die("CYTOSIG_CORE_FAILED",
             f"cytosig core rc={proc.returncode}: "
             f"{proc.stderr[-400:]}")
    scores = pd.read_csv(res_tsv, sep="\t")
    expr_tsv.unlink(missing_ok=True)
    res_tsv.unlink(missing_ok=True)

    # per_cell：追加群汇总段（beta/zscore 群均值，样本名 = 群名+后缀）
    if mode == "per_cell":
        cell_group = groups.reindex(Y.columns).fillna("NA")
        extra = []
        for kind in ("beta", "zscore"):
            piv = (scores[scores["kind"] == kind]
                   .pivot(index="factor", columns="sample", values="value"))
            agg = piv.T.groupby(cell_group).mean().T  # 因子×群
            long = agg.reset_index().melt(id_vars="factor",
                                          var_name="sample",
                                          value_name="value")
            long.insert(0, "kind", f"{kind}_by_group")
            extra.append(long)
        scores = pd.concat([scores] + extra, ignore_index=True)

    csv_path = out_dir / "cytosig_scores.csv"
    scores.to_csv(csv_path, index=False)
    hm_png, top_png = _plot(scores[scores["kind"].isin(("beta", "zscore"))]
                            if mode == "per_cell" else scores, out_dir)

    beta = (scores[scores["kind"] == "beta"]
            .pivot(index="factor", columns="sample", values="value"))
    zs = (scores[scores["kind"] == "zscore"]
          .pivot(index="factor", columns="sample", values="value"))
    top_b = {s: [(i, round(float(v), 4)) for i, v in
                 beta[s].sort_values(ascending=False).head(5).items()]
             for s in beta.columns}
    top_z = {s: [(i, round(float(v), 4)) for i, v in
                 zs[s].sort_values(ascending=False).head(5).items()]
             for s in zs.columns}
    emit({"ok": True, "dataset_ref": dataset_ref, "mode": mode,
          "groupby": groupby, "n_factors": int(beta.shape[0]),
          "n_genes_used": int(expr.shape[0]),
          "n_samples": int(beta.shape[1]), "nrand": nrand,
          "top_by_beta": top_b, "top_by_zscore": top_z,
          "scores_csv": str(csv_path), "heatmap_png": str(hm_png),
          "top_png": str(top_png),
          "note": "beta=signal strength (CytoSig website ranking); "
                  "zscore=permutation significance — rankings may differ, "
                  "do not mix. Matrix source: "
                  + ("adata.raw" if adata.raw is not None else "adata.X")
                  + ("; per_cell group summary in kind=*_by_group rows"
                     if mode == "per_cell" else "")})


if __name__ == "__main__":
    run(main)
