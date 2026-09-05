# Phase 34 B 类分析 Python 等价物 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 bio:cpu-latest 镜像内新增 sc_cellchat（liana 细胞通讯）/ sc_milo（差异丰度）/ sc_deconv（bulk 解卷积）三个 L1 工具，sc_* 工具数 13→16。

**Architecture:** 沿用 BioRunner 脚本约定（stdin JSON → stdout JSON，图/csv 落 /ws）。cellchat/milo 读 processed.h5ad；deconv 额外读 /data 挂载下的 bulk 矩阵（复用 sc_load 的 resolve_data_path 白名单 + mounts 机制）。设计文档：`docs/superpowers/specs/2026-09-05-phase34-b-class-python.md`。

**Tech Stack:** scanpy/anndata、liana（新）、statsmodels、scikit-learn、scipy（后三者 scanpy 传递依赖已带入）。

**环境惯例（沿用 Phase 31-33）：**
- pytest 一律加 `--basetemp=.pytest_tmp`（用户 Temp 目录 ACL 损坏）
- 全量回归输出重定向 `.pytest_last.log` 再 Select-String 取统计行（kernel idle sweeper 日志会盖住摘要）
- 宿主冒烟用双/三 WS_ROOT+DATA_ROOT monkeypatch（`from common import WS_ROOT` 是值绑定，需同时 patch common 与各脚本模块）
- git commit 单行 `-m`（PowerShell 无 heredoc）；git add 精确列文件，排除 toolsv1//code_workspace/.pytest_tmp 等 untracked
- docker 重建后断网容器验证 import；服务重启用 `scripts\start.ps1`（输出有缓冲，以 pidfile + err.log 判定）

---

### Task 1: cellchat.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/cellchat.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_cellchat：细胞通讯分析（Phase 34，对齐 toolsv1 server_cellchat 单组推断）。

stdin: {"dataset_id": ..., "celltype_col": "leiden", "species": "human",
        "expr_prop": 0.1, "min_cells": 10, "top_n": 30}
需 processed.h5ad（sc_process 产物，含 raw）。
liana 内置 cellchat 方法 + 随包资源库（human=consensus / mouse=mouseconsensus），
容器断网可用。多组比较（toolsv1 part1-5 套件）本版不做。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata) -> str:
    """列出可作细胞标签的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _find_col(df: pd.DataFrame, preferred: str, suffix: str) -> str:
    """防御式列解析：liana 版本间列名可能漂移，按首选名→后缀兜底。"""
    if preferred in df.columns:
        return preferred
    for c in df.columns:
        if str(c).endswith(suffix):
            return str(c)
    raise KeyError(
        f"cannot locate score/pval column ({preferred!r} or *{suffix}); "
        f"liana columns: {list(df.columns)}")


def main() -> None:
    """主流程：liana cellchat → LR 全量 csv + top LR dotplot + 互作计数热图。"""
    import liana as li
    import matplotlib.pyplot as plt

    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    species = str(args.get("species", "human")).strip().lower()
    expr_prop = float(args.get("expr_prop", 0.1))
    min_cells = int(args.get("min_cells", 10))
    top_n = int(args.get("top_n", 30))
    resource = {"human": "consensus", "mouse": "mouseconsensus"}.get(species)
    if resource is None:
        raise ValueError(f"species must be human or mouse, got {species!r}")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        raise ValueError(
            f"celltype column {celltype_col!r} not in obs; available: "
            f"{_cat_cols(adata)}")
    labels = adata.obs[celltype_col].astype(str)
    dropped = labels.value_counts().loc[lambda s: s < min_cells].index.tolist()
    if dropped:
        adata = adata[~labels.isin(dropped)].copy()
        labels = adata.obs[celltype_col].astype(str)
    if labels.nunique() < 2:
        raise ValueError(
            f"need >=2 cell types with >= {min_cells} cells each; "
            f"dropped too-small: {dropped}")

    li.mt.cellchat(adata, groupby=celltype_col, resource_name=resource,
                   expr_prop=expr_prop, use_raw=True, verbose=False)
    lr = adata.uns["liana_res"].copy()
    score_col = _find_col(lr, "lr_probs", "probs")
    pval_col = _find_col(lr, "cellchat_pvals", "pvals")
    lr = lr.rename(columns={score_col: "lr_score", pval_col: "pval"})
    lr["pair"] = lr["source"].astype(str) + "->" + lr["target"].astype(str)
    lr["lr"] = (lr["ligand_complex"].astype(str) + "|"
                + lr["receptor_complex"].astype(str))
    lr = lr.sort_values(["pval", "lr_score"],
                        ascending=[True, False]).reset_index(drop=True)

    out_dir = WS_ROOT / args["dataset_id"] / "cellchat"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "cellchat_lr.csv"
    lr.to_csv(csv_path, index=False)

    # dotplot：top LR 对（行）× top 细胞类型对（列），大小=-log10(p)，色=lr_score
    sig = lr[lr["pval"] < 0.05]
    top_lrs = lr["lr"].drop_duplicates().head(top_n).tolist()
    top_pairs = (sig["pair"].value_counts().head(12).index.tolist()
                 or lr["pair"].drop_duplicates().head(12).tolist())
    sub = lr[lr["lr"].isin(top_lrs) & lr["pair"].isin(top_pairs)]
    fig, ax = plt.subplots(figsize=(max(4, 0.9 * len(top_pairs) + 2),
                                    max(4, 0.32 * len(top_lrs) + 2)))
    for yi, lr_name in enumerate(top_lrs):
        for xi, pair in enumerate(top_pairs):
            hit = sub[(sub["lr"] == lr_name) & (sub["pair"] == pair)]
            if hit.empty:
                continue
            r = hit.iloc[0]
            ax.scatter(xi, yi,
                       s=20 + 120 * -np.log10(max(r["pval"], 1e-12)) / 12,
                       c=[r["lr_score"]], cmap="viridis", vmin=0,
                       vmax=float(lr["lr_score"].max()) or 1.0,
                       linewidths=0)
    ax.set_xticks(range(len(top_pairs)))
    ax.set_xticklabels(top_pairs, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(top_lrs)))
    ax.set_yticklabels(top_lrs, fontsize=7)
    ax.set_title(f"top LR interactions ({species}, {resource})", fontsize=10)
    fig.tight_layout()
    dot_png = out_dir / "cellchat_dotplot.png"
    fig.savefig(dot_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 互作计数热图：source × target 显著互作数
    types = sorted(labels.unique().tolist())
    mat = pd.DataFrame(0, index=types, columns=types)
    for _, r in sig.iterrows():
        mat.loc[str(r["source"]), str(r["target"])] += 1
    fig, ax = plt.subplots(figsize=(max(4, 0.7 * len(types) + 2),
                                    max(3, 0.6 * len(types) + 2)))
    im = ax.imshow(mat.to_numpy(), cmap="Reds")
    ax.set_xticks(range(len(types)), types, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(types)), types, fontsize=8)
    ax.set_xlabel("target")
    ax.set_ylabel("source")
    ax.set_title("significant interaction counts (p<0.05)", fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    heat_png = out_dir / "cellchat_heatmap.png"
    fig.savefig(heat_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top = [
        {"ligand": str(r["ligand_complex"]),
         "receptor": str(r["receptor_complex"]),
         "source": str(r["source"]), "target": str(r["target"]),
         "lr_score": round(float(r["lr_score"]), 3),
         "pval": float(f"{r['pval']:.2e}")}
        for _, r in lr.head(top_n).iterrows()]

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "celltype_col": celltype_col,
        "resource": resource,
        "n_celltypes": int(labels.nunique()),
        "dropped_small_types": [str(d) for d in dropped],
        "n_pairs_tested": int(len(lr)),
        "n_sig": int(len(sig)),
        "top": top,
        "csv": str(csv_path),
        "dotplot_png": str(dot_png),
        "heatmap_png": str(heat_png),
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `python -m ruff check sandbox/sc_tools/cellchat.py`
Expected: 无新告警（F401/I001 等用 `ruff check --fix` 修）

---

### Task 2: milo.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/milo.py`

- [ ] **Step 1: 写脚本**

> 实施修正：NB-GLM(α=1) → QP-GLM(Poisson+全局Pearson离散度 floor=1)，原因见验证记录

```python
"""sc_milo：差异丰度分析（Phase 34，Python 复刻 miloR 思路）。

stdin: {"dataset_id": ..., "sample_col": "sample", "group_col": "condition",
        "group_a": "treated", "group_b": "control", "k": 0, "top_n": 20}
需 processed.h5ad。流程：KNN 图（复用 connectivities，缺则重建）→
refined 式邻域采样（与已留邻域重叠>0.8 的种子跳过）→ 样本×邻域计数 →
逐邻域 NB-GLM（offset=log 样本总细胞数）→ BH 校正。
口径声明：非 edgeR 准似然模型；BH 非 miloR SpatialFDR 加权校正——
结果为近似口径，显著性偏保守，详见 emit 的 method_note。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata) -> str:
    """列出可作分组的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    """两邻域（升序唯一索引数组）重叠度：交集 / 较小集合。"""
    inter = np.intersect1d(a, b, assume_unique=True).size
    return inter / max(1, min(a.size, b.size))


def main() -> None:
    """主流程：邻域采样 → 逐邻域 NB-GLM → BH → da csv + UMAP 着色图。"""
    import matplotlib.pyplot as plt
    import scanpy as sc
    import statsmodels.api as sm
    from statsmodels.stats.multitest import multipletests

    args = read_args()
    sample_col = str(args.get("sample_col", "")).strip()
    group_col = str(args.get("group_col", "")).strip()
    group_a = str(args.get("group_a", "")).strip()
    group_b = str(args.get("group_b", "")).strip()
    if not (sample_col and group_col and group_a and group_b):
        raise ValueError(
            "sample_col/group_col/group_a/group_b are all required, e.g. "
            "{'sample_col': 'sample', 'group_col': 'condition', "
            "'group_a': 'treated', 'group_b': 'control'}")
    k_arg = int(args.get("k", 0))
    top_n = int(args.get("top_n", 20))

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    for col in (sample_col, group_col):
        if col not in adata.obs:
            raise ValueError(
                f"column {col!r} not in obs; available: {_cat_cols(adata)}")
    samples = adata.obs[sample_col].astype(str)
    grp_per_sample = adata.obs.groupby(samples)[group_col].agg(
        lambda s: s.astype(str).unique().tolist())
    bad = grp_per_sample[grp_per_sample.map(len) > 1]
    if not bad.empty:
        raise ValueError(
            f"samples with mixed {group_col} values: {bad.index.tolist()[:10]}")
    sample_group = grp_per_sample.map(lambda v: v[0])
    for g in (group_a, group_b):
        if g not in set(sample_group):
            raise ValueError(
                f"group value {g!r} not found per-sample; existing: "
                f"{sorted(set(sample_group))[:20]}")
    n_sa = int((sample_group == group_a).sum())
    n_sb = int((sample_group == group_b).sum())
    if n_sa < 2 or n_sb < 2:
        raise ValueError(
            f"need >=2 samples per group: {group_a}={n_sa}, {group_b}={n_sb}")

    # KNN 图：优先复用 sc_process 的 connectivities
    if "connectivities" not in adata.obsp:
        sc.pp.neighbors(adata, n_neighbors=15)
    conn = adata.obsp["connectivities"].tocsr()

    n_cells = adata.n_obs
    if k_arg > 0:
        k = k_arg
    else:
        k = int(np.clip(round(0.1 * samples.value_counts().min()), 10, 50))

    # refined 式邻域采样：随机序种子，重叠>0.8 跳过
    rng = np.random.default_rng(42)
    kept: list[int] = []
    kept_nbrs: list[np.ndarray] = []
    for i in rng.permutation(n_cells):
        nbr = np.unique(
            np.append(conn.indices[conn.indptr[i]:conn.indptr[i + 1]], i))
        if nbr.size < 3:
            continue
        if any(_overlap(nbr, kn) > 0.8 for kn in kept_nbrs):
            continue
        kept.append(int(i))
        kept_nbrs.append(nbr)
    if len(kept) < 5:
        raise ValueError(f"too few neighbourhoods sampled: {len(kept)}")

    # 样本×邻域计数
    sample_cats = pd.Categorical(samples)
    codes = sample_cats.codes
    sample_names = sample_cats.categories.tolist()
    counts = np.zeros((len(kept), len(sample_names)), dtype=int)
    for row, nbr in enumerate(kept_nbrs):
        counts[row] = np.bincount(codes[nbr], minlength=len(sample_names))

    # 逐邻域 NB-GLM：count ~ group，offset=log(样本总细胞数)
    totals = samples.value_counts().reindex(sample_names).to_numpy()
    x = sample_group.reindex(sample_names).map(
        {group_b: 0.0, group_a: 1.0}).to_numpy()
    X = sm.add_constant(x)
    offset = np.log(totals.astype(float))
    logfc = np.full(len(kept), np.nan)
    pvals = np.full(len(kept), np.nan)
    for row in range(len(kept)):
        try:
            fit = sm.GLM(counts[row], X, offset=offset,
                         family=sm.families.NegativeBinomial(alpha=1.0)
                         ).fit()
            logfc[row] = fit.params[1] / np.log(2)
            pvals[row] = fit.pvalues[1]
        except Exception:  # noqa: BLE001 —— 单邻域拟合失败置 nan，BH 跳过
            continue
    ok = ~np.isnan(pvals)
    fdr = np.full(len(kept), np.nan)
    fdr[ok] = multipletests(pvals[ok], method="fdr_bh")[1]

    # 邻域注释：多数 leiden 标签与构成比
    labels = (adata.obs["leiden"].astype(str)
              if "leiden" in adata.obs
              else pd.Series(["NA"] * n_cells))
    maj, maj_frac = [], []
    for nbr in kept_nbrs:
        vc = labels.iloc[nbr].value_counts()
        maj.append(str(vc.index[0]))
        maj_frac.append(round(float(vc.iloc[0]) / nbr.size, 3))

    df = pd.DataFrame({
        "nhood": range(len(kept)),
        "index_cell": kept,
        "n_cells": [n.size for n in kept_nbrs],
        "log2fc": logfc,
        "pval": pvals,
        "fdr": fdr,
        "majority_label": maj,
        "majority_frac": maj_frac,
    }).sort_values("fdr")

    out_dir = WS_ROOT / args["dataset_id"] / "milo"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"milo_{group_a}_vs_{group_b}.csv"
    df.to_csv(csv_path, index=False)

    # UMAP：灰底 + 种子细胞按 log2fc 着色（FDR<0.1 加黑边）
    fig, ax = plt.subplots(figsize=(6, 5))
    umap = adata.obsm["X_umap"]
    ax.scatter(umap[:, 0], umap[:, 1], s=4, c="#d8d8d8", linewidths=0)
    vmax = float(np.nanmax(np.abs(logfc))) or 1.0
    seed_xy = umap[np.array(kept)]
    sc_plot = ax.scatter(seed_xy[:, 0], seed_xy[:, 1], s=18, c=logfc,
                         cmap="RdBu_r", vmin=-vmax, vmax=vmax,
                         linewidths=0)
    sig_mask = fdr < 0.1
    if sig_mask.any():
        ax.scatter(seed_xy[sig_mask, 0], seed_xy[sig_mask, 1], s=26,
                   facecolors="none", edgecolors="black", linewidths=0.8)
    fig.colorbar(sc_plot, ax=ax, shrink=0.8, label="log2FC")
    ax.set_title(f"milo DA: {group_a}(n={n_sa}) vs {group_b}(n={n_sb}), "
                 f"k={k}, {len(kept)} nhoods", fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    png_path = out_dir / f"milo_{group_a}_vs_{group_b}_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top = [
        {"nhood": int(r["nhood"]), "log2fc": round(float(r["log2fc"]), 2),
         "fdr": float(f"{r['fdr']:.2e}"),
         "majority_label": str(r["majority_label"]),
         "n_cells": int(r["n_cells"])}
        for _, r in df.head(top_n).iterrows()
        if not np.isnan(r["fdr"])]

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "comparison": f"{group_a}_vs_{group_b}",
        "sample_col": sample_col, "group_col": group_col,
        "n_samples_a": n_sa, "n_samples_b": n_sb,
        "k": k, "n_nhoods": len(kept),
        "n_sig_fdr01": int((fdr < 0.1).sum()),
        "top": top,
        "csv": str(csv_path),
        "umap_png": str(png_path),
        "method_note": ("Python 复刻：NB-GLM(alpha=1)+BH；非 miloR edgeR QL "
                        "模型与 SpatialFDR 加权校正，显著性口径偏保守"),
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `python -m ruff check sandbox/sc_tools/milo.py`
Expected: 无新告警

---

### Task 3: deconv.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/deconv.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_deconv：bulk 解卷积（Phase 34，对齐 toolsv1 server_bulk_deconvolution）。

stdin: {"dataset_id": ..., "celltype_col": "leiden",
        "bulk_path": "<rel-under-/data>", "method": "wnnls", "top_n": 200}
bulk 矩阵为 /data 挂载下 csv/tsv（行=基因、列=样本；若转置方向与 sc
参考基因交集更大则自动转置并在 transpose_note 说明）。
method=wnnls：MuSiC 式加权 NNLS（权重=1/sqrt(参考内基因跨细胞方差)）；
method=nusvr：CIBERSORT 式线性 NuSVR（简化版，无 nu 调参特征选择）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import DATA_ROOT, WS_ROOT, emit, load_adata, read_args, run


def _cat_cols(adata) -> str:
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
        lbl.set_ha("right")
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
```

- [ ] **Step 2: ruff 检查**

Run: `python -m ruff check sandbox/sc_tools/deconv.py`
Expected: 无新告警

---

### Task 4: 注册 3 个 ToolSpec + handler

**Files:**
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`（docstring 行 1；handler 追加在 sc_cellfreq handler 约 222 行后；ToolSpec 追加在 sc_cellfreq ToolSpec 约 572 行后）

- [ ] **Step 1: docstring 更新**

行 1 由 `"""Phase 20/31/32：单细胞 sc_* 工具注册（spec §4；9 个 L1_compute 工具）。` 改为 `"""Phase 20/31/32/33/34：单细胞 sc_* 工具注册（spec §4；16 个 L1_compute 工具）。`

- [ ] **Step 2: 追加三个 handler（sc_cellfreq handler 之后）**

```python
    def sc_cellchat(*, dataset_ref: str, celltype_col: str = "leiden",
                    species: str = "human", expr_prop: float = 0.1,
                    min_cells: int = 10, top_n: int = 30) -> dict:
        """细胞通讯（Phase 34）：liana cellchat → LR 表+dotplot+热图。"""
        try:
            out = runner.run("cellchat", {
                "dataset_id": dataset_ref, "celltype_col": celltype_col,
                "species": species, "expr_prop": expr_prop,
                "min_cells": min_cells, "top_n": top_n,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_milo(*, dataset_ref: str, sample_col: str, group_col: str,
                group_a: str, group_b: str, k: int = 0,
                top_n: int = 20) -> dict:
        """差异丰度（Phase 34）：KNN 邻域 + NB-GLM → da csv+UMAP。"""
        try:
            out = runner.run("milo", {
                "dataset_id": dataset_ref, "sample_col": sample_col,
                "group_col": group_col, "group_a": group_a,
                "group_b": group_b, "k": k, "top_n": top_n,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_deconv(*, dataset_ref: str, bulk_file: str,
                  celltype_col: str = "leiden", method: str = "wnnls",
                  top_n: int = 200) -> dict:
        """bulk 解卷积（Phase 34）：wNNLS/NuSVR → 比例 csv+图。

        bulk_file 复用 sc_load 的数据根白名单校验与 /data 挂载。
        """
        try:
            mount_root, rel, host = runner.resolve_data_path(bulk_file)
            out = runner.run(
                "deconv",
                {"dataset_id": dataset_ref, "bulk_path": rel,
                 "celltype_col": celltype_col, "method": method,
                 "top_n": top_n},
                mounts=[(mount_root, "/data")],
            )
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

- [ ] **Step 3: 追加三个 ToolSpec（sc_cellfreq ToolSpec 之后）**

```python
    registry.register(ToolSpec(
        name="sc_cellchat",
        description=(
            "细胞通讯分析（Phase 34，对齐 server_cellchat 单组推断）："
            "推断细胞类型间的配体-受体互作（liana cellchat 方法 + 内置"
            "consensus 资源库）。输出显著 LR 对 top 表、全量 csv、top LR "
            "dotplot 与细胞类型间互作计数热图。回答\"哪类细胞在给谁发"
            "信号\"类问题。需先跑 sc_process。列名错误时错误消息会列出"
            "可用列。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "细胞标签列（leiden 或注释列）"},
                "species": {"type": "string", "default": "human",
                            "enum": ["human", "mouse"]},
                "expr_prop": {"type": "number", "default": 0.1,
                              "description": "细胞类型中表达比例阈值"},
                "min_cells": {"type": "integer", "default": 10,
                              "description": "细胞类型最少细胞数（低于剔除）"},
                "top_n": {"type": "integer", "default": 30, "maximum": 100},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_cellchat,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_milo",
        description=(
            "差异丰度分析（Phase 34，Python 复刻 miloR 思路）：在 KNN 图"
            "邻域上检验\"哪些细胞状态在 A 组比 B 组显著增多/减少\""
            "（如病灶富集的细胞亚群）。逐邻域 NB-GLM + BH 校正，输出"
            "da csv 与 UMAP 着色图（FDR<0.1 邻域黑边高亮）。"
            "需先跑 sc_process；obs 需含样本列与分组列，且每组样本数≥2。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "sample_col": {"type": "string",
                               "description": "样本/受试者列（如 sample）"},
                "group_col": {"type": "string",
                              "description": "分组列（如 condition）"},
                "group_a": {"type": "string",
                            "description": "对比组（log2FC>0 方向）取值"},
                "group_b": {"type": "string", "description": "参照组取值"},
                "k": {"type": "integer", "default": 0,
                      "description": "邻域大小；0=自动 clip(0.1×最小样本量,"
                                     "10,50)"},
                "top_n": {"type": "integer", "default": 20, "maximum": 100},
            },
            "required": ["dataset_ref", "sample_col", "group_col",
                         "group_a", "group_b"],
        },
        risk_level="L1_compute",
        handler=sc_milo,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_deconv",
        description=(
            "bulk 解卷积（Phase 34，对齐 server_bulk_deconvolution）："
            "以当前单细胞数据为参考，估计 bulk 表达矩阵中各细胞类型比例"
            "（method=wnnls MuSiC 式加权 NNLS / nusvr CIBERSORT 式线性"
            "SVR）。bulk_file 为数据目录内 csv/tsv（行=基因列=样本，"
            "方向放反会自动转置）。输出比例 csv、signature csv、堆叠柱状"
            "图与热图。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc 参考的 dataset_ref"},
                "bulk_file": {"type": "string",
                              "description": "bulk 表达矩阵本地路径（必须在"
                                             "管理员允许的数据目录内）"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "参考细胞标签列"},
                "method": {"type": "string", "default": "wnnls",
                           "enum": ["wnnls", "nusvr"]},
                "top_n": {"type": "integer", "default": 200, "maximum": 2000,
                          "description": "signature 基因数"},
            },
            "required": ["dataset_ref", "bulk_file"],
        },
        risk_level="L1_compute",
        handler=sc_deconv,
        timeout_sec=1200,
    ))
```

- [ ] **Step 4: ruff 检查**

Run: `python -m ruff check orchestrator/tools/builtin/l3_singlecell.py`
Expected: 无新告警

---

### Task 5: 单元测试（TDD：注册断言 + 转发 + 错误自纠）

**Files:**
- Modify: `tests/unit/test_l3_singlecell.py`（文件尾追加）

- [ ] **Step 1: 追加 7 个用例**

```python
# === Phase 34：B 类分析（细胞通讯/差异丰度/bulk 解卷积） ===


def test_sc_phase34_tools_registered_l1(tmp_path):
    """三新工具注册可见且 L1_compute（研究链路可规划）。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_cellchat", "sc_milo", "sc_deconv"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_cellchat_forwards_params(tmp_path):
    """handler 转发 cellchat 参数（species→资源库、min_cells）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "resource": "consensus",
        "n_sig": 12, "top": [{"ligand": "CXCL12"}]}
    out = reg.get("sc_cellchat").handler(
        dataset_ref="d", celltype_col="leiden", species="human")
    args = runner.run.call_args.args
    assert args[0] == "cellchat"
    assert args[1]["celltype_col"] == "leiden"
    assert args[1]["species"] == "human"
    assert "ok" not in out
    assert out["top"][0]["ligand"] == "CXCL12"
    assert reg.get("sc_cellchat").timeout_sec == 1800


def test_sc_cellchat_error_lists_columns(tmp_path):
    """BioRunError → 透传（细胞标签列不存在时错误含可用列引导自纠）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR",
        "ValueError: celltype column 'celltype' not in obs; available: "
        "leiden(5)")
    out = reg.get("sc_cellchat").handler(
        dataset_ref="d", celltype_col="celltype")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert "available" in out["error_message"]


def test_sc_milo_forwards_params(tmp_path):
    """handler 转发 milo 参数（样本列+分组列+定向对比）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "comparison": "treated_vs_control",
        "n_nhoods": 40, "n_sig_fdr01": 3, "top": []}
    out = reg.get("sc_milo").handler(
        dataset_ref="d", sample_col="sample", group_col="condition",
        group_a="treated", group_b="control")
    args = runner.run.call_args.args
    assert args[0] == "milo"
    assert args[1]["sample_col"] == "sample"
    assert args[1]["group_a"] == "treated"
    assert out["n_sig_fdr01"] == 3
    assert reg.get("sc_milo").timeout_sec == 1800


def test_sc_milo_error_passthrough(tmp_path):
    """样本数不足等脚本错误透传。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR",
        "ValueError: need >=2 samples per group: treated=1, control=3")
    out = reg.get("sc_milo").handler(
        dataset_ref="d", sample_col="sample", group_col="condition",
        group_a="treated", group_b="control")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert ">=2 samples" in out["error_message"]


def test_sc_deconv_forwards_params(tmp_path):
    """bulk_file 走 resolve_data_path 白名单 + /data 挂载转发。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.return_value = (
        Path("D:/sc_data"), "bulk.csv", "D:/sc_data/bulk.csv")
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "wnnls",
        "n_samples": 3, "dominant": []}
    out = reg.get("sc_deconv").handler(
        dataset_ref="d", bulk_file="D:/sc_data/bulk.csv")
    assert runner.resolve_data_path.call_args.args[0] == "D:/sc_data/bulk.csv"
    call = runner.run.call_args
    assert call.args[0] == "deconv"
    assert call.args[1]["bulk_path"] == "bulk.csv"
    assert call.kwargs["mounts"] == [(Path("D:/sc_data"), "/data")]
    assert "ok" not in out


def test_sc_deconv_schema_enum(tmp_path):
    """method enum 锁定 wnnls/nusvr。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_deconv").parameters["properties"]
    assert props["method"]["enum"] == ["wnnls", "nusvr"]
    assert reg.get("sc_deconv").parameters["required"] == [
        "dataset_ref", "bulk_file"]
```

- [ ] **Step 2: 跑新用例**

Run: `python -m pytest tests/unit/test_l3_singlecell.py -q --basetemp=.pytest_tmp`
Expected: `34 passed`（存量 27 + 新增 7）

- [ ] **Step 3: ruff 检查**

Run: `python -m ruff check tests/unit/test_l3_singlecell.py`
Expected: 无新告警

---

### Task 6: Dockerfile liana 层 + 宿主安装

**Files:**
- Modify: `sandbox/bio.Dockerfile`（bbknn 层后追加）

- [ ] **Step 1: Dockerfile 追加独立层（保上方缓存层）**

```dockerfile
# Phase 34 B 类分析：liana（细胞通讯，内置 consensus/mouseconsensus
# 资源库随包分发，容器断网可用）。statsmodels/sklearn 已由 scanpy
# 传递依赖带入；liana 依赖 plotnine/kneed 等均为纯 Python/manylinux 轮，
# 无编译需求（若构建报编译错误，按 bbknn 层先例同层临时装 g++）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple liana
```

- [ ] **Step 2: 宿主 venv 装 liana（供本机冒烟）**

Run: `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple liana`
Expected: Successfully installed liana-*

- [ ] **Step 3: 宿主验证 liana 资源库离线可用（不联网 import + 选库）**

Run: `python -c "import liana as li; r = li.resource.select_resource('consensus'); print(r.shape)"`
Expected: 打印资源表形状（如 (几千, 若干列)）；若报联网下载错误 → 转构建期预取方案（参考 Phase 31 fetch_gene_sets.py 模式），并更新本计划

---

### Task 7: 本机冒烟（假数据生物学自洽）

**Files:**
- Create: `code_workspace/smoke_phase34.py`（untracked，不入 git）

- [ ] **Step 1: 写冒烟脚本**

```python
"""Phase 34 三脚本本机冒烟（宿主 venv 直跑，monkeypatch WS_ROOT/DATA_ROOT）。

假数据设计（300 细胞 × 80 基因）：
- leiden 3 簇（0/1/2 各 100）；sample s1-s4（s1/s2=control，s3/s4=treated）
- cellchat：簇 0 强表达 CXCL12、簇 1 强表达 CXCR4 → 期望 LR 表含 CXCL12
- milo：簇 2 在 treated 两样本中占比 ~2 倍 → 期望显著邻域多数标签=2 且 log2FC>0
- deconv：由参考 signature 按已知比例人工混合 3 个 bulk → 期望还原误差<0.1
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"i:\飞书agent\sandbox\sc_tools")
WS = Path(r"i:\飞书agent\code_workspace\smoke_ws34")
DATA = Path(r"i:\飞书agent\code_workspace\smoke_data34")
WS.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(7)
genes = [f"G{i:03d}" for i in range(78)] + ["CXCL12", "CXCR4"]
n_per_cluster = 100
labels = np.repeat(["0", "1", "2"], n_per_cluster)
X = rng.poisson(0.3, size=(300, len(genes))).astype(float)
# 簇特异基因（供 leiden 真实结构 + deconv signature 可区分）
X[labels == "0", 0:10] += rng.poisson(5, (100, 10))
X[labels == "1", 10:20] += rng.poisson(5, (100, 10))
X[labels == "2", 20:30] += rng.poisson(5, (100, 10))
# cellchat 强 LR 对
X[labels == "0", 78] += rng.poisson(8, 100)   # CXCL12 in cluster 0
X[labels == "1", 79] += rng.poisson(8, 100)   # CXCR4 in cluster 1

samples = np.repeat(["s1", "s2", "s3", "s4"], 75)
condition = np.where(np.isin(samples, ["s3", "s4"]), "treated", "control")
# milo：簇 2 的细胞尽量落在 s3/s4（打乱到目标比例 ~2 倍）
idx = np.arange(300)
c2 = idx[labels == "2"]
np.random.default_rng(11).shuffle(c2)
samples[c2[:50]] = "s3"
samples[c2[50:]] = "s4"
condition = np.where(np.isin(samples, ["s3", "s4"]), "treated", "control")

import anndata as ad
import scanpy as sc

obs = pd.DataFrame({"leiden": labels, "sample": samples,
                    "condition": condition},
                   index=[f"c{i}" for i in range(300)])
adata = ad.AnnData(X=X, obs=obs, var=pd.DataFrame(index=genes))
adata.raw = adata
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=min(60, len(genes)),
                            subset=True)
sc.pp.scale(adata, max_value=10)
sc.pp.pca(adata, n_comps=20)
sc.pp.neighbors(adata, n_neighbors=15)
sc.tl.umap(adata)
adata.obs["leiden"] = labels  # 保留手工 3 簇标签（分析口径一致）

ds = WS / "ds34"
ds.mkdir(exist_ok=True)
adata.write(ds / "processed.h5ad")

# deconv 用 bulk：参考 CPM 均值按已知比例混合
raw = adata.raw.to_adata()
Xr = raw.X
lib = Xr.sum(1)
lib[lib == 0] = 1
cpm = Xr / lib[:, None] * 1e4
S = pd.DataFrame({t: cpm[labels == t].mean(0) for t in ["0", "1", "2"]},
                 index=raw.var_names)
true_props = pd.DataFrame({"b1": [0.7, 0.2, 0.1],
                           "b2": [0.1, 0.7, 0.2],
                           "b3": [0.33, 0.33, 0.34]},
                          index=["0", "1", "2"])
bulk = S.values @ true_props.values
pd.DataFrame(bulk, index=S.index,
             columns=true_props.columns).to_csv(DATA / "bulk34.csv")


def run_script(name: str, payload: dict) -> dict:
    """三 monkeypatch 后子进程跑脚本（WS_ROOT/DATA_ROOT 值绑定需双补丁）。"""
    driver = (
        "import sys, pathlib; "
        "sys.path.insert(0, r'%s'); "
        "import common; "
        "common.WS_ROOT = pathlib.Path(r'%s'); "
        "common.DATA_ROOT = pathlib.Path(r'%s'); "
        "import %s as m; m.WS_ROOT = common.WS_ROOT; "
        "m.DATA_ROOT = common.DATA_ROOT if hasattr(m, 'DATA_ROOT') else common.DATA_ROOT; "
        "m.run(m.main)" % (ROOT, WS, DATA, name))
    proc = subprocess.run(
        [sys.executable, "-c", driver], input=json.dumps(payload),
        capture_output=True, text=True, timeout=1200)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"], out
    return out


# 1) cellchat：LR 表含 CXCL12（配体），且存在 0->1 方向互作
r1 = run_script("cellchat", {"dataset_id": "ds34", "celltype_col": "leiden",
                             "species": "human", "expr_prop": 0.05,
                             "min_cells": 5})
lr = pd.read_csv(r1["csv"])
hit = lr[(lr["ligand_complex"].str.contains("CXCL12"))
         & (lr["source"] == "0") & (lr["target"] == "1")]
assert len(lr) > 0 and len(hit) > 0, "CXCL12 0->1 interaction missing"
print("[ok] cellchat: n_pairs=%d n_sig=%d, CXCL12 0->1 found" % (
    r1["n_pairs_tested"], r1["n_sig"]))

# 2) milo：显著邻域存在且 top 多数标签=2、log2FC>0
r2 = run_script("milo", {"dataset_id": "ds34", "sample_col": "sample",
                         "group_col": "condition", "group_a": "treated",
                         "group_b": "control", "k": 15})
assert r2["n_sig_fdr01"] > 0, "no significant nhood"
top = r2["top"][0]
assert top["majority_label"] == "2" and top["log2fc"] > 0, top
print("[ok] milo: n_nhoods=%d n_sig=%d top=%s" % (
    r2["n_nhoods"], r2["n_sig_fdr01"], top))

# 3) deconv：wnnls 还原已知比例，最大误差 < 0.1
r3 = run_script("deconv", {"dataset_id": "ds34", "celltype_col": "leiden",
                           "bulk_path": "bulk34.csv", "method": "wnnls",
                           "top_n": 200})
est = pd.read_csv(r3["proportions_csv"], index_col=0).T
err = (est - true_props).abs().max().max()
assert err < 0.1, f"deconv max error {err}"
print("[ok] deconv: max abs error = %.3f" % err)

print("SMOKE PASS: phase34 all three scripts biologically consistent")
```

- [ ] **Step 2: 跑冒烟**

Run: `python code_workspace/smoke_phase34.py`
Expected: 三行 `[ok]` + `SMOKE PASS`；若 cellchat 因 liana API/列名漂移失败 → 按 stderr 调整 `_find_col` 或调用签名（liana 版本差异是本任务已知风险点）

---

### Task 8: docker 重建 + 断网容器验证

- [ ] **Step 1: 重建镜像**

Run: `docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile`
Expected: 成功；记录新镜像 ID（docker images 首行）

- [ ] **Step 2: 断网容器 import + liana 资源库验证**

Run: `docker run --rm --network none feishu-research-agent/bio:cpu-latest python -c "import sys; sys.path.insert(0,'/opt/sc_tools'); import liana as li; r = li.resource.select_resource('consensus'); import cellchat, milo, deconv; print('offline ok', r.shape)"`
Expected: `offline ok (....)`；若资源库需联网 → 构建期预取方案（fetch 到 /opt/gene_sets/ 同类目录并改 cellchat.py 指向），重建验证

---

### Task 9: 全量回归

- [ ] **Step 1: 回归**

Run: `python -m pytest tests -q --basetemp=.pytest_tmp > .pytest_last.log 2>&1; Select-String -Path .pytest_last.log -Pattern 'passed' | Select-Object -Last 1`
Expected: `1032 passed`（1025 + 7 新用例），0 failed

---

### Task 10: 文档同步 + commit + 服务重启

**Files:**
- Modify: `docs/ROADMAP.md`（+Phase 34 行）
- Modify: `docs/平台功能说明书.md`（6.1 节标题 13→16 工具；表格 +3 行）
- Modify: `docs/使用说明书.md`（场景 D 产物表 +3 行；触发话术：通讯/差异丰度/解卷积）
- Modify: `测试总结+2026-09-04T15-10-00.md`（+Phase 34 节；统一测试轮待办 +3 工具）

- [ ] **Step 1: 四文档同步**（内容范式仿 Phase 33 对应段落）

- [ ] **Step 2: commit**

```powershell
git add sandbox/sc_tools/cellchat.py sandbox/sc_tools/milo.py sandbox/sc_tools/deconv.py sandbox/bio.Dockerfile orchestrator/tools/builtin/l3_singlecell.py tests/unit/test_l3_singlecell.py docs/superpowers/plans/2026-09-05-phase34-b-class-python.md docs/ROADMAP.md "docs/使用说明书.md" "docs/平台功能说明书.md" "测试总结+2026-09-04T15-10-00.md"
git commit -m "feat: Phase 34 B 类分析 Python 等价物三件套——sc_cellchat（liana cellchat+consensus 资源离线）+ sc_milo（KNN 邻域 NB-GLM 复刻 miloR，口径偏保守注明）+ sc_deconv（MuSiC 式 wNNLS + CIBERSORT 式 nusvr，bulk 走数据根白名单挂载）；本机冒烟生物学自洽（CXCL12 0->1/簇2 富集邻域/解卷积误差<0.1）+ 断网容器 import ok；TDD 7 用例回归 1032 全过；真机验收延后统一测试轮，双说明书/ROADMAP/测试总结同步"
```

（commit message 中镜像 ID 与回归数字按实际替换）

- [ ] **Step 3: 服务重启对齐 HEAD**

```powershell
$old = Get-Content .ws_client.pid; Stop-Process -Id $old -Force -ErrorAction SilentlyContinue
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

Expected: `[PASS] ws_client 已启动：pid=xxxx HEAD=<新commit>`；err.log 尾行含 `ws long-connection starting` 与 Lark connected

---

## 验证记录

**冒烟输出**（宿主假数据，生物学自洽）：
- cellchat：n_pairs=126，n_sig=18，CXCL12 簇0→簇1 命中
- milo：n_nhoods=285，n_sig=79，top 邻域 majority=簇2，log2fc=2.58
- deconv：wnnls 还原已知比例，最大误差 0.000

**镜像**：edcdbc2e → 342ef18a738f（断网容器 16 模块 import + liana
资源库 (4620,2) 验证通过）

**回归**：单测 34 passed（27+7）；全量回归 1032 passed（1025+7），51.27s

**milo 口径变更**：NB-GLM（固定 α=1）→ QP-GLM（Poisson + 全局 Pearson
离散度 floor=1）+ BH；复刻 miloR 思路但口径偏保守，非 edgeR
QL/SpatialFDR。

**事故与教训**：
1. liana 的 assert_covered 要求资源基因覆盖率≥98%（硬编码），假数据
   须用 consensus 真实基因名，否则断言失败；
2. milo 初版固定 α=1 的 NB-GLM 在 2v2 小样本次数下 Wald SE≈1、
   FDR<0.1 数学不可达 → 改 QP-GLM；小样本 GLM 设计须先做功效估算；
3. liana 1.10.0 要求 pandas<3，宿主与容器 pandas 均降级 3.0.5→2.3.3，
   全量回归与 16 模块断网 import 验证无碍。

**commit hash**：40ac00c


