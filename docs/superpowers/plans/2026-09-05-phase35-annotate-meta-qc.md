# Phase 35 注释与质控补强四件套 实施计划（spec+plan 合并）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 sc_annotate（CellTypist/marker 双路注释）/ sc_meta（元数据编辑）/ sc_doublet（scrublet 双联体）/ sc_cellcycle（细胞周期）四个 L1 工具，sc_* 工具数 16→20。

**Architecture:** 沿用 BioRunner 约定。**写回模式**：四工具均把结果列原地写回 processed.h5ad（与 subcluster 的新 ref 模式互补）。annotate/cellcycle 用 `adata.raw`（= log-norm 全基因，process.py L45 确认）作打分输入；doublet 需整数 counts → 读 filtered/raw 链按 obs_names 交集写回。

**Tech Stack:** celltypist（新，模型构建期预取）、scrublet（新，annoy 需 g++ 同层编译）、scanpy 内置 score_genes_cell_cycle、statsmodels 无新增。

**关键技术事实（已核实）**：
- processed.h5ad：X = HVG 子集 + scaled；`adata.raw` = **log-normalized 全基因**（非 counts）——annotate/cellcycle 直接用 raw；doublet 不能用 raw
- filtered.h5ad = QC 后 counts（未归一化）——doublet 输入
- 宿主/容器 pandas 现为 2.3.3（Phase 34 liana 降级后）
- pytest 加 `--basetemp=.pytest_tmp`；回归落 `.pytest_last.log` 取 `\d+ passed` 行；venv = `.\.venv\Scripts\python.exe`（系统 python 是 WindowsApps 版无 ruff/pytest）
- 冒烟 monkeypatch：值绑定需双补丁（common 与各脚本模块的 WS_ROOT/DATA_ROOT/MODEL_DIR）

---

### Task 1: annotate.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/annotate.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_annotate：细胞类型注释（Phase 35，对齐 toolsv1 server_cell_annotation）。

stdin: {"dataset_id": ..., "method": "celltypist"|"markers",
        "model": "Immune_All_Low.pkl",              # celltypist 路
        "marker_sets": {"T cell": ["CD3D", ...]},   # markers 路
        "celltype_col": "leiden",                   # 簇标签列（投票/平滑）
        "out_col": "annotation"}                    # markers 路写回列名
需 processed.h5ad。两路结果均原地写回 processed.h5ad：
- celltypist 路：obs 增 celltypist_label/celltypist_conf。adata.raw 为
  log-norm 全基因，正好是 celltypist 期望输入；majority voting 以
  celltype_col 为 over_clustering。模型文件在 /opt/celltypist_models/
  （构建期预取，断网可用），model 不存在时错误列出现有模型。
- markers 路：逐集 score_genes(use_raw=True) → 簇均值 argmax →
  obs[out_col]；得分矩阵落 csv 供审阅。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run

MODEL_DIR = Path("/opt/celltypist_models")


def _cat_cols(adata) -> str:
    """列出可作分组的 obs 列（2..50 个取值），错误消息引导 planner 自纠。"""
    cols = []
    for c in adata.obs.columns:
        n = adata.obs[c].astype(str).nunique()
        if 2 <= n <= 50:
            cols.append(f"{c}({n})")
    return ", ".join(cols) or "<none>"


def _umap_by_label(adata, col: str, png_path: Path, title: str) -> None:
    """按标签列手绘 UMAP（避免 scanpy 多类图例截断）。"""
    import matplotlib.pyplot as plt

    labels = adata.obs[col].astype(str)
    cats = sorted(labels.unique().tolist())
    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(7, 5.5))
    umap = adata.obsm["X_umap"]
    for i, c in enumerate(cats):
        m = (labels == c).to_numpy()
        ax.scatter(umap[m, 0], umap[m, 1], s=5, color=cmap(i % 20),
                   label=f"{c} ({int(m.sum())})", linewidths=0)
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.0, 0.5),
              markerscale=2)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _run_celltypist(adata, model: str, celltype_col: str,
                    out_dir: Path) -> dict:
    """CellTypist 参考注释：本地模型 + celltype_col 级 majority voting。"""
    import celltypist

    model_path = MODEL_DIR / model
    if not model_path.exists():
        avail = sorted(p.name for p in MODEL_DIR.glob("*.pkl"))
        raise ValueError(
            f"model {model!r} not found under {MODEL_DIR}; "
            f"available: {avail}")
    ref = adata.raw.to_adata()  # processed 的 raw = log-norm 全基因
    ref.obs[celltype_col] = adata.obs[celltype_col].astype(str).to_numpy()
    pred = celltypist.annotate(ref, model=str(model_path),
                               majority_voting=True,
                               over_clustering=celltype_col)
    labels = pred.predicted_labels["majority_voting"].astype(str)
    conf = pred.predicted_labels["conf_score"].astype(float)
    labels.index = adata.obs_names
    conf.index = adata.obs_names
    adata.obs["celltypist_label"] = labels
    adata.obs["celltypist_conf"] = conf

    ct = pd.crosstab(adata.obs[celltype_col].astype(str), labels)
    csv_path = out_dir / "annotate_celltypist_by_cluster.csv"
    ct.to_csv(csv_path)
    png_path = out_dir / "annotate_celltypist_umap.png"
    _umap_by_label(adata, "celltypist_label", png_path,
                   f"celltypist ({model})")
    counts = labels.value_counts()
    return {
        "label_col": "celltypist_label",
        "n_labels": int(len(counts)),
        "counts": {str(k): int(v) for k, v in counts.head(20).items()},
        "mean_conf": round(float(conf.mean()), 3),
        "csv": str(csv_path),
        "umap_png": str(png_path),
    }


def _run_markers(adata, marker_sets, celltype_col: str, out_col: str,
                 out_dir: Path) -> dict:
    """marker 基因集路：逐集打分 → 簇均值 argmax 定标签。"""
    import scanpy as sc

    if not isinstance(marker_sets, dict) or not marker_sets:
        raise ValueError(
            "marker_sets required for method=markers, e.g. "
            '{"T cell": ["CD3D", "CD3E"], "B cell": ["MS4A1"]}')
    if len(marker_sets) > 20:
        raise ValueError(f"too many marker sets: {len(marker_sets)} (>20)")
    raw_genes = (set(adata.raw.var_names.astype(str))
                 if adata.raw is not None
                 else set(adata.var_names.astype(str)))

    scored, skipped = [], []
    for name, genes in marker_sets.items():
        inter = [str(g) for g in (genes or []) if str(g) in raw_genes]
        if len(inter) < 2:
            skipped.append({"set": str(name), "found": inter})
            continue
        safe = re.sub(r"\W+", "_", str(name))
        sc.tl.score_genes(adata, inter, score_name=f"ann_{safe}",
                          use_raw=True)
        scored.append((str(name), f"ann_{safe}"))
    if not scored:
        raise ValueError(
            "all marker sets skipped (<2 genes found in data); "
            "check gene naming (symbol vs ensembl)")

    grp = adata.obs[celltype_col].astype(str)
    means = {name: adata.obs.groupby(grp, observed=True)[col].mean()
             for name, col in scored}
    M = pd.DataFrame(means)  # 簇 × 集
    assign = M.idxmax(axis=1)
    adata.obs[out_col] = grp.map(assign).astype(str)

    csv_path = out_dir / "annotate_markers_scores.csv"
    M.to_csv(csv_path, index_label=celltype_col)
    png_path = out_dir / "annotate_markers_umap.png"
    _umap_by_label(adata, out_col, png_path,
                   f"marker annotation ({len(scored)} sets)")
    counts = adata.obs[out_col].value_counts()
    return {
        "label_col": out_col,
        "n_labels": int(len(counts)),
        "counts": {str(k): int(v) for k, v in counts.head(20).items()},
        "cluster_assignment": {str(k): str(v) for k, v in assign.items()},
        "skipped_sets": skipped,
        "csv": str(csv_path),
        "umap_png": str(png_path),
    }


def main() -> None:
    """主流程：按 method 分派两路注释，结果原地写回 processed.h5ad。"""
    args = read_args()
    method = str(args.get("method", "celltypist")).strip().lower()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    out_col = str(args.get("out_col", "annotation")).strip()

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if celltype_col not in adata.obs:
        raise ValueError(
            f"celltype column {celltype_col!r} not in obs; available: "
            f"{_cat_cols(adata)}")
    if adata.raw is None:
        raise ValueError("processed.h5ad missing raw; re-run sc_process")

    out_dir = WS_ROOT / args["dataset_id"] / "annotate"
    out_dir.mkdir(parents=True, exist_ok=True)

    if method == "celltypist":
        res = _run_celltypist(adata, str(args.get("model",
                                                  "Immune_All_Low.pkl")),
                              celltype_col, out_dir)
    elif method == "markers":
        res = _run_markers(adata, args.get("marker_sets"), celltype_col,
                           out_col, out_dir)
    else:
        raise ValueError(f"method must be celltypist or markers, "
                         f"got {method!r}")

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        **res,
        "saved": str(h5ad_path),
        "note": "标签列已写回 processed.h5ad，下游 sc_plot/sc_cellfreq/"
                "sc_cellchat 的 celltype_col 可直接引用",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/annotate.py`
Expected: 零告警（可自动修复项用 `--fix`）

---

### Task 2: meta.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/meta.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_meta：元数据编辑（Phase 35，对齐 toolsv1 server_meta_settings/file_io）。

stdin: {"dataset_id": ..., "op": "merge_csv"|"map_values"|"rename_col", ...}
- merge_csv: {"csv_path": "<rel-under-/data>", "key_col": "sample"}
  csv 首列=key，其余列按 key 并入 obs；与现有列同名时加 _csv 后缀
  （防覆盖，conflict 列表在 note 返回）
- map_values: {"col": "leiden", "mapping": {"0": "T cell"}, "out_col": "..."}
  未映射取值保留原值；out_col 缺省 = {col}_mapped
- rename_col: {"old": "orig.ident", "new": "sample"}
写回 processed.h5ad 原地保存。merge_csv 的 csv 走 /data 挂载
（handler 层 resolve_data_path 白名单）。
"""
from __future__ import annotations

import pandas as pd
from common import DATA_ROOT, WS_ROOT, emit, load_adata, read_args, run


def main() -> None:
    """主流程：按 op 分派三种编辑，结果原地写回 processed.h5ad。"""
    args = read_args()
    op = str(args.get("op", "")).strip()
    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    n_cells = adata.n_obs
    note = ""

    if op == "merge_csv":
        csv_path = str(args.get("csv_path", "")).strip()
        key_col = str(args.get("key_col", "")).strip()
        if not (csv_path and key_col):
            raise ValueError("merge_csv needs csv_path and key_col")
        if key_col not in adata.obs:
            raise ValueError(f"key column {key_col!r} not in obs; "
                             f"existing: {list(adata.obs.columns)[:20]}")
        p = (DATA_ROOT / csv_path).resolve()
        if DATA_ROOT not in p.parents:
            raise ValueError(f"invalid csv_path: {csv_path!r}")
        table = pd.read_csv(p, sep=None, engine="python", index_col=0)
        table.index = table.index.astype(str)
        key = adata.obs[key_col].astype(str)
        conflicts, added = [], []
        for c in table.columns:
            target = str(c)
            if target in adata.obs:
                target = f"{c}_csv"
                conflicts.append(str(c))
            mapped = key.map(table[str(c)].astype(str))
            n_na = int(mapped.isna().sum())
            adata.obs[target] = mapped
            added.append({"col": target, "unmatched_cells": n_na})
        if conflicts:
            note = f"columns renamed with _csv suffix: {conflicts}"
        changed = [a["col"] for a in added]
        detail = added

    elif op == "map_values":
        col = str(args.get("col", "")).strip()
        mapping = args.get("mapping")
        out_col = str(args.get("out_col", "")).strip() or f"{col}_mapped"
        if not col or not isinstance(mapping, dict) or not mapping:
            raise ValueError("map_values needs col and mapping dict, e.g. "
                             '{"col": "leiden", "mapping": {"0": "T cell"}}')
        if col not in adata.obs:
            raise ValueError(f"column {col!r} not in obs; "
                             f"existing: {list(adata.obs.columns)[:20]}")
        src = adata.obs[col].astype(str)
        mapped = src.map({str(k): str(v) for k, v in mapping.items()})
        adata.obs[out_col] = mapped.fillna(src)
        unmapped = sorted(set(src) - set(str(k) for k in mapping))
        if unmapped:
            note = f"unmapped values kept as-is: {unmapped[:20]}"
        changed = [out_col]
        detail = {"values": adata.obs[out_col].value_counts()
                  .head(20).to_dict()}

    elif op == "rename_col":
        old = str(args.get("old", "")).strip()
        new = str(args.get("new", "")).strip()
        if not (old and new):
            raise ValueError("rename_col needs old and new")
        if old not in adata.obs:
            raise ValueError(f"column {old!r} not in obs; "
                             f"existing: {list(adata.obs.columns)[:20]}")
        if new in adata.obs:
            raise ValueError(f"target column {new!r} already exists")
        adata.obs[new] = adata.obs.pop(old)
        changed = [new]
        detail = {"renamed": f"{old} -> {new}"}

    else:
        raise ValueError(f"op must be merge_csv/map_values/rename_col, "
                         f"got {op!r}")

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "op": op,
        "changed_cols": changed,
        "n_cells": n_cells,
        "detail": detail,
        "note": note,
        "saved": str(h5ad_path),
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/meta.py`
Expected: 零告警

---

### Task 3: doublet.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/doublet.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_doublet：双联体检测（Phase 35，scrublet；对齐 scop RunDoubletCalling）。

stdin: {"dataset_id": ..., "expected_rate": 0.06, "n_prin_comps": 30,
        "celltype_col": "leiden"}
scrublet 需整数 counts（processed 的 raw 是 log-norm 不可直接用）→
读 filtered/raw 回退链取 counts，按 obs_names 交集写回 processed.h5ad：
obs 增 doublet_score / predicted_doublet（只标记不删除，过滤走 sc_qc
语义）。自动阈值失败时回退 expected_rate 分位数阈值并在 note 说明。
产物：doublet_score UMAP（预测双联体黑圈）+ 各簇双联体率 csv。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run


def main() -> None:
    """主流程：counts 上跑 scrublet → 写回 processed → UMAP + 簇率 csv。"""
    import matplotlib.pyplot as plt
    import scrublet as scr

    args = read_args()
    expected_rate = float(args.get("expected_rate", 0.06))
    n_prin_comps = int(args.get("n_prin_comps", 30))
    celltype_col = str(args.get("celltype_col", "leiden")).strip()

    counts_ad = load_adata({"dataset_id": args["dataset_id"], "file": "any"})
    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    common_cells = adata.obs_names.intersection(counts_ad.obs_names)
    if len(common_cells) < 100:
        raise ValueError(
            f"too few cells shared between counts and processed: "
            f"{len(common_cells)} (<100)")
    sub = counts_ad[common_cells]
    npc = max(2, min(n_prin_comps, sub.n_vars - 1, sub.n_obs - 1))

    scrub = scr.Scrublet(sub.X, expected_doublet_rate=expected_rate)
    score, pred = scrub.scrub_doublets(
        min_counts=2, min_cells=3, min_gene_variability_pctl=85,
        n_prin_comps=npc)
    note = ""
    if pred is None:  # 自动阈值失败（双峰不明显）→ 分位数回退
        thr = float(np.quantile(score, 1.0 - expected_rate))
        pred = score > thr
        note = (f"auto threshold failed; quantile fallback at "
                f"1-expected_rate ({thr:.3f})")

    score_s = pd.Series(np.asarray(score, dtype=float), index=common_cells)
    pred_s = pd.Series(np.asarray(pred, dtype=bool), index=common_cells)
    adata.obs["doublet_score"] = score_s.reindex(adata.obs_names)
    adata.obs["predicted_doublet"] = pred_s.reindex(adata.obs_names)

    out_dir = WS_ROOT / args["dataset_id"] / "doublet"
    out_dir.mkdir(parents=True, exist_ok=True)
    if celltype_col in adata.obs:
        rate = (adata.obs.groupby(celltype_col, observed=True)
                ["predicted_doublet"].agg(["mean", "count"])
                .rename(columns={"mean": "doublet_rate",
                                 "count": "n_cells"})
                .sort_values("doublet_rate", ascending=False))
        csv_path = out_dir / "doublet_rate_by_cluster.csv"
        rate.to_csv(csv_path)
    else:
        csv_path = ""

    fig, ax = plt.subplots(figsize=(6.5, 5))
    umap = adata.obsm["X_umap"]
    sc_plt = ax.scatter(umap[:, 0], umap[:, 1], s=5,
                        c=adata.obs["doublet_score"].to_numpy(
                            dtype=float, na_value=np.nan),
                        cmap="viridis", linewidths=0)
    mask = adata.obs["predicted_doublet"].fillna(False).to_numpy(dtype=bool)
    if mask.any():
        ax.scatter(umap[mask, 0], umap[mask, 1], s=14, facecolors="none",
                   edgecolors="red", linewidths=0.6)
    fig.colorbar(sc_plt, ax=ax, shrink=0.8, label="doublet score")
    ax.set_title(f"scrublet doublets (expected_rate={expected_rate}, "
                 f"predicted={int(mask.sum())})", fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    png_path = out_dir / "doublet_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_cells_scored": int(len(common_cells)),
        "n_doublets": int(mask.sum()),
        "doublet_rate": round(float(mask.sum() / adata.n_obs), 4),
        "doublet_score_quantiles": {
            "p50": round(float(score_s.quantile(0.5)), 3),
            "p90": round(float(score_s.quantile(0.9)), 3),
            "p99": round(float(score_s.quantile(0.99)), 3)},
        "csv": str(csv_path),
        "umap_png": str(png_path),
        "saved": str(h5ad_path),
        "note": note or "只标记不删除；过滤请用 sc_qc 语义另行决定",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/doublet.py`
Expected: 零告警

---

### Task 4: cellcycle.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/cellcycle.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_cellcycle：细胞周期打分（Phase 35，Tirosh 2016 S/G2M 基因集内嵌）。

stdin: {"dataset_id": ..., "celltype_col": "leiden"}
需 processed.h5ad。S/G2M 基因集（Tirosh 2016，Seurat cc.genes 同款）
内嵌常量，断网可用；score_genes_cell_cycle 在 raw（log-norm 全基因）
上打分 → obs 增 S_score/G2M_score/phase，原地写回 processed.h5ad。
产物：phase UMAP + phase×簇计数 csv。基因交集 <5 报错提示物种不匹配
（内置为人源基因名）。
"""
from __future__ import annotations

import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run

S_GENES = [
    "MCM5", "PCNA", "TYMS", "FEN1", "MCM2", "MCM4", "RRM1", "UNG",
    "GINS2", "MCM6", "CDCA7", "DTL", "PRIM1", "UHRF1", "HELLS", "RFC2",
    "RPA2", "NASP", "RAD51AP1", "GMNN", "WDR76", "SLBP", "CCNE2", "UBR7",
    "POLD3", "MSH2", "ATAD2", "RAD51", "RRM2", "CDC45", "CDC6", "EXO1",
    "TIPIN", "DSCC1", "BLM", "CASP8AP2", "USP1", "CLSPN", "POLA1",
    "CHAF1B", "BRIP1", "E2F8",
]

G2M_GENES = [
    "HMGB2", "CDK1", "NUSAP1", "UBE2C", "BIRC5", "TPX2", "TOP2A",
    "NDC80", "CKS2", "NUF2", "CKS1B", "MKI67", "TMPO", "CENPF", "TACC3",
    "PIMREG", "SMC4", "CCNB2", "CKAP2L", "CKAP2", "AURKB", "BUB1",
    "KIF11", "ANP32E", "TUBB4B", "GTSE1", "KIF20B", "HJURP", "CDCA3",
    "HN1", "CDC20", "TTK", "CDC25C", "KIF2C", "RANGAP1", "NCAPD2",
    "DLGAP5", "CDCA2", "CDCA8", "ECT2", "KIF23", "HMMR", "AURKA",
    "PSRC1", "ANLN", "LBR", "CKAP5", "CENPE", "CTCF", "NEK2", "G2E3",
    "GAS2L3", "CBX5", "CENPA",
]


def main() -> None:
    """主流程：raw 上打 S/G2M 分 → phase 写回 → UMAP + 计数 csv。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if adata.raw is None:
        raise ValueError("processed.h5ad missing raw; re-run sc_process")
    raw_genes = set(adata.raw.var_names.astype(str))
    s_found = [g for g in S_GENES if g in raw_genes]
    g_found = [g for g in G2M_GENES if g in raw_genes]
    if len(s_found) < 5 or len(g_found) < 5:
        raise ValueError(
            f"too few cell-cycle genes found (S={len(s_found)}, "
            f"G2M={len(g_found)}); built-in sets are human gene symbols, "
            "check species / gene naming")

    sc.tl.score_genes_cell_cycle(adata, s_genes=s_found, g2m_genes=g_found,
                                 use_raw=True)

    out_dir = WS_ROOT / args["dataset_id"] / "cellcycle"
    out_dir.mkdir(parents=True, exist_ok=True)
    phase = adata.obs["phase"].astype(str)
    ct = pd.crosstab(adata.obs[celltype_col].astype(str)
                     if celltype_col in adata.obs
                     else pd.Series(["all"] * adata.n_obs),
                     phase)
    csv_path = out_dir / "cellcycle_phase_counts.csv"
    ct.to_csv(csv_path)

    fig, ax = plt.subplots(figsize=(6.5, 5))
    umap = adata.obsm["X_umap"]
    colors = {"G1": "#b8b8b8", "S": "#d84b3b", "G2M": "#3b7dd8"}
    for ph in ("G1", "S", "G2M"):
        m = (phase == ph).to_numpy()
        if m.any():
            ax.scatter(umap[m, 0], umap[m, 1], s=5, c=colors[ph],
                       label=f"{ph} ({int(m.sum())})", linewidths=0)
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5),
              markerscale=2)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("cell cycle phase (Tirosh S/G2M)", fontsize=10)
    fig.tight_layout()
    png_path = out_dir / "cellcycle_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    counts = phase.value_counts()
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "s_genes_found": len(s_found),
        "g2m_genes_found": len(g_found),
        "phase_counts": {str(k): int(v) for k, v in counts.items()},
        "csv": str(csv_path),
        "umap_png": str(png_path),
        "saved": str(h5ad_path),
        "note": "S_score/G2M_score/phase 已写回 processed.h5ad",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/cellcycle.py`
Expected: 零告警

---

### Task 5: 注册 4 个 ToolSpec + handler

**Files:**
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`（docstring 行 1；handler 追加在 sc_deconv handler 之后；ToolSpec 追加在 sc_deconv ToolSpec 之后）

- [ ] **Step 1: docstring 更新**

`Phase 20/31/32/33/34` → `Phase 20/31/32/33/34/35`，`16 个` → `20 个`

- [ ] **Step 2: 追加四个 handler（sc_deconv handler 之后）**

```python
    def sc_annotate(*, dataset_ref: str, method: str = "celltypist",
                    model: str = "Immune_All_Low.pkl",
                    marker_sets: dict | None = None,
                    celltype_col: str = "leiden",
                    out_col: str = "annotation") -> dict:
        """细胞注释（Phase 35）：celltypist 参考 / marker 打分双路。"""
        try:
            out = runner.run("annotate", {
                "dataset_id": dataset_ref, "method": method,
                "model": model, "marker_sets": marker_sets,
                "celltype_col": celltype_col, "out_col": out_col,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_meta(*, dataset_ref: str, op: str, col: str = "",
                mapping: dict | None = None, out_col: str = "",
                csv_file: str = "", key_col: str = "",
                old: str = "", new: str = "") -> dict:
        """元数据编辑（Phase 35）：merge_csv/map_values/rename_col。"""
        try:
            payload = {
                "dataset_id": dataset_ref, "op": op, "col": col,
                "mapping": mapping, "out_col": out_col,
                "key_col": key_col, "old": old, "new": new,
            }
            if op == "merge_csv":
                mount_root, rel, host = runner.resolve_data_path(csv_file)
                payload["csv_path"] = rel
                out = runner.run("meta", payload,
                                 mounts=[(mount_root, "/data")])
            else:
                out = runner.run("meta", payload)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_doublet(*, dataset_ref: str, expected_rate: float = 0.06,
                   n_prin_comps: int = 30,
                   celltype_col: str = "leiden") -> dict:
        """双联体检测（Phase 35）：scrublet → 写回 doublet 列。"""
        try:
            out = runner.run("doublet", {
                "dataset_id": dataset_ref, "expected_rate": expected_rate,
                "n_prin_comps": n_prin_comps, "celltype_col": celltype_col,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_cellcycle(*, dataset_ref: str,
                     celltype_col: str = "leiden") -> dict:
        """细胞周期打分（Phase 35）：Tirosh S/G2M → 写回 phase 列。"""
        try:
            out = runner.run("cellcycle", {
                "dataset_id": dataset_ref, "celltype_col": celltype_col,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

- [ ] **Step 3: 追加四个 ToolSpec（sc_deconv ToolSpec 之后）**

```python
    registry.register(ToolSpec(
        name="sc_annotate",
        description=(
            "细胞类型注释（Phase 35，对齐 server_cell_annotation）："
            "method=celltypist 用参考模型自动注释（写回 celltypist_label/"
            "celltypist_conf，model 不存在时错误列出可用模型）；"
            "method=markers 用用户 marker 基因集打分按簇投票（写回 "
            "out_col 指定列）。注释列写回 processed.h5ad——sc_plot/"
            "sc_cellfreq/sc_cellchat 的 celltype_col 可直接引用。"
            "需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "method": {"type": "string", "default": "celltypist",
                           "enum": ["celltypist", "markers"]},
                "model": {"type": "string", "default": "Immune_All_Low.pkl",
                          "description": "celltypist 模型文件名（如 "
                                         "Immune_All_Low/High.pkl）"},
                "marker_sets": {
                    "type": "object",
                    "additionalProperties": {"type": "array",
                                             "items": {"type": "string"}},
                    "description": "markers 路必填：{细胞类型: [基因,...]}，"
                                   "≤20 集"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "簇标签列（投票/平滑用）"},
                "out_col": {"type": "string", "default": "annotation",
                            "description": "markers 路写回的列名"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_annotate,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_meta",
        description=(
            "元数据编辑（Phase 35，对齐 server_meta_settings）：对话式修改"
            "细胞注释表。op=merge_csv 把样本注释表（数据目录内 csv，首列"
            "=连接键）按 key_col 并入 obs（典型：给样本补 condition 分组"
            "列）；op=map_values 取值映射/合并（如 leiden 簇号改细胞类型"
            "名）；op=rename_col 列改名。结果写回 processed.h5ad。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "op": {"type": "string",
                       "enum": ["merge_csv", "map_values", "rename_col"]},
                "col": {"type": "string", "default": "",
                        "description": "map_values 的源列"},
                "mapping": {"type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": "map_values 的 {旧值: 新值}"},
                "out_col": {"type": "string", "default": "",
                            "description": "map_values 输出列（缺省 "
                                           "{col}_mapped）"},
                "csv_file": {"type": "string", "default": "",
                             "description": "merge_csv 的 csv 本地路径"
                                            "（须在数据目录内）"},
                "key_col": {"type": "string", "default": "",
                            "description": "merge_csv 的 obs 连接列"},
                "old": {"type": "string", "default": "",
                        "description": "rename_col 的原列名"},
                "new": {"type": "string", "default": "",
                        "description": "rename_col 的新列名"},
            },
            "required": ["dataset_ref", "op"],
        },
        risk_level="L1_compute",
        handler=sc_meta,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_doublet",
        description=(
            "双联体检测（Phase 35，scrublet）：识别两个细胞被包进同一"
            "液滴形成的假细胞。在 QC 后 counts 上打分，写回 doublet_score/"
            "predicted_doublet 到 processed.h5ad（只标记不删除）。输出"
            "各簇双联体率 csv 与 UMAP（预测双联体红圈）。expected_rate "
            "默认 0.06（10x 典型值，按上机细胞量调整）。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "expected_rate": {"type": "number", "default": 0.06,
                                  "description": "预期双联体率"},
                "n_prin_comps": {"type": "integer", "default": 30},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "簇率统计用标签列"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_doublet,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_cellcycle",
        description=(
            "细胞周期打分（Phase 35）：Tirosh S/G2M 基因集（人源，内嵌"
            "离线）给每细胞定 G1/S/G2M 期，写回 S_score/G2M_score/phase "
            "到 processed.h5ad，输出 phase UMAP 与 phase×簇计数 csv。"
            "回答\"增殖活性差异/周期是否干扰聚类\"类问题。需先跑 "
            "sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "celltype_col": {"type": "string", "default": "leiden"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_cellcycle,
        timeout_sec=600,
    ))
```

- [ ] **Step 4: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check orchestrator/tools/builtin/l3_singlecell.py`
Expected: 零告警

---

### Task 6: 单元测试（9 用例）

**Files:**
- Modify: `tests/unit/test_l3_singlecell.py`（文件尾追加）

- [ ] **Step 1: 追加 9 个用例**

```python
# === Phase 35：注释与质控补强（annotate/meta/doublet/cellcycle） ===


def test_sc_phase35_tools_registered_l1(tmp_path):
    """四新工具注册可见且 L1_compute。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_annotate", "sc_meta", "sc_doublet", "sc_cellcycle"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_annotate_celltypist_forwards(tmp_path):
    """celltypist 路转发 model 与 celltype_col。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "celltypist",
        "label_col": "celltypist_label", "n_labels": 5}
    out = reg.get("sc_annotate").handler(
        dataset_ref="d", method="celltypist", model="Immune_All_High.pkl")
    args = runner.run.call_args.args
    assert args[0] == "annotate"
    assert args[1]["method"] == "celltypist"
    assert args[1]["model"] == "Immune_All_High.pkl"
    assert "ok" not in out
    assert reg.get("sc_annotate").timeout_sec == 1800


def test_sc_annotate_markers_forwards(tmp_path):
    """markers 路转发 marker_sets 字典。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "markers",
        "label_col": "annotation",
        "cluster_assignment": {"0": "T cell"}}
    out = reg.get("sc_annotate").handler(
        dataset_ref="d", method="markers",
        marker_sets={"T cell": ["CD3D", "CD3E"]})
    args = runner.run.call_args.args
    assert args[1]["marker_sets"] == {"T cell": ["CD3D", "CD3E"]}
    assert out["cluster_assignment"]["0"] == "T cell"


def test_sc_annotate_schema_enum(tmp_path):
    """method enum 锁定 celltypist/markers。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_annotate").parameters["properties"]
    assert props["method"]["enum"] == ["celltypist", "markers"]


def test_sc_meta_merge_csv_mounts(tmp_path):
    """merge_csv 走 resolve_data_path 白名单 + /data 挂载转发。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.return_value = (
        Path("D:/sc_data"), "meta.csv", "D:/sc_data/meta.csv")
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "op": "merge_csv",
        "changed_cols": ["condition"]}
    out = reg.get("sc_meta").handler(
        dataset_ref="d", op="merge_csv", csv_file="D:/sc_data/meta.csv",
        key_col="sample")
    call = runner.run.call_args
    assert call.args[0] == "meta"
    assert call.args[1]["csv_path"] == "meta.csv"
    assert call.args[1]["key_col"] == "sample"
    assert call.kwargs["mounts"] == [(Path("D:/sc_data"), "/data")]
    assert "ok" not in out


def test_sc_meta_map_values_forwards(tmp_path):
    """map_values 不挂 /data，mapping 透传。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "op": "map_values",
        "changed_cols": ["celltype"]}
    out = reg.get("sc_meta").handler(
        dataset_ref="d", op="map_values", col="leiden",
        mapping={"0": "T"}, out_col="celltype")
    call = runner.run.call_args
    assert call.args[1]["mapping"] == {"0": "T"}
    assert call.args[1]["out_col"] == "celltype"
    assert call.kwargs.get("mounts") is None
    runner.resolve_data_path.assert_not_called()


def test_sc_meta_error_passthrough(tmp_path):
    """脚本错误透传（列不存在等）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "ValueError: column 'foo' not in obs")
    out = reg.get("sc_meta").handler(dataset_ref="d", op="rename_col",
                                     old="foo", new="bar")
    assert out["error_code"] == "SC_SCRIPT_ERROR"


def test_sc_doublet_forwards_params(tmp_path):
    """doublet 转发 expected_rate；timeout 1200。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "n_doublets": 12,
        "doublet_rate": 0.05}
    out = reg.get("sc_doublet").handler(dataset_ref="d", expected_rate=0.08)
    args = runner.run.call_args.args
    assert args[0] == "doublet"
    assert args[1]["expected_rate"] == 0.08
    assert out["n_doublets"] == 12
    assert reg.get("sc_doublet").timeout_sec == 1200


def test_sc_cellcycle_forwards_params(tmp_path):
    """cellcycle 转发；timeout 600。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d",
        "phase_counts": {"G1": 150, "S": 30, "G2M": 20}}
    out = reg.get("sc_cellcycle").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[0] == "cellcycle"
    assert out["phase_counts"]["G1"] == 150
    assert reg.get("sc_cellcycle").timeout_sec == 600
```

- [ ] **Step 2: 跑新用例**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_l3_singlecell.py -q --basetemp=.pytest_tmp`
Expected: `43 passed`（34 + 9）

- [ ] **Step 3: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check tests/unit/test_l3_singlecell.py`
Expected: 零告警

---

### Task 7: Dockerfile 两层 + 宿主安装

**Files:**
- Modify: `sandbox/bio.Dockerfile`（liana 层后追加两层）

- [ ] **Step 1: Dockerfile 追加**

```dockerfile
# Phase 35 自动注释：celltypist + 模型构建期预取（运行期断网可用）。
# 模型下载自 celltypist.cog.sanger.ac.uk；不可达时 build 报错重试即可。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple celltypist \
    && python -c "from celltypist import models; \
        models.download_models(model=['Immune_All_Low.pkl', 'Immune_All_High.pkl'])" \
    && mkdir -p /opt/celltypist_models \
    && cp /root/.celltypist/data/models/*.pkl /opt/celltypist_models/

# Phase 35 双联体：scrublet（依赖 annoy 无 manylinux 轮需源码编译——
# 同层临时装 g++，编译完成后 purge，bbknn 层先例）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple scrublet \
    && apt-get purge -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: 宿主 venv 安装（供冒烟）**

Run: `.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple celltypist scrublet`
Expected: Successfully installed celltypist-* scrublet-*（annoy 在 Windows 有预编译轮，无需编译器）

- [ ] **Step 3: 宿主预取 celltypist 模型（冒烟用）**

Run: `.\.venv\Scripts\python.exe -c "from celltypist import models; models.download_models(model=['Immune_All_Low.pkl']); print(models.models_path)"`
Expected: 打印模型目录路径（冒烟 driver 会把它 patch 给 annotate.MODEL_DIR）；若 sanger 站点不可达 → 报告错误，转手工下载方案

---

### Task 8: 本机冒烟（四脚本生物学自洽）

**Files:**
- Create: `code_workspace/smoke_phase35.py`（untracked）

- [ ] **Step 1: 写冒烟脚本**

```python
"""Phase 35 四脚本本机冒烟（宿主 venv 直跑，monkeypatch WS/DATA/MODEL_DIR）。

假数据设计（300 细胞）：
- 3 簇带真实 marker：簇0=T(CD3D/CD3E)、簇1=B(MS4A1/CD79A)、簇2=NK(NKG7/GZMB)
- 簇 2 前半 S 期基因高表达（cellcycle 验证）
- 双联体：filtered.h5ad 中 20 个细胞为随机两细胞 counts 之和
- meta：csv 给 sample 列补 condition；map_values 把 leiden 改类名
- celltypist：真实 Immune_All_Low 模型跑通（假数据基因覆盖低，
  只验证流程与写回，不做生物学断言）
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"i:\飞书agent\sandbox\sc_tools")
WS = Path(r"i:\飞书agent\code_workspace\smoke_ws35")
DATA = Path(r"i:\飞书agent\code_workspace\smoke_data35")
from celltypist import models as _ctm
MODELS = Path(_ctm.models_path)  # 宿主已预取 Immune_All_Low.pkl
WS.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(7)
S10 = ["MCM5", "PCNA", "TYMS", "FEN1", "MCM2", "MCM4", "RRM1", "UNG",
       "GINS2", "MCM6"]
G2M10 = ["HMGB2", "CDK1", "NUSAP1", "UBE2C", "BIRC5", "TPX2", "TOP2A",
         "NDC80", "CKS2", "NUF2"]
MARKERS = ["CD3D", "CD3E", "MS4A1", "CD79A", "NKG7", "GZMB"]
genes = MARKERS + S10 + G2M10 + [f"G{i:03d}" for i in range(40)]
n = 300
labels = np.repeat(["0", "1", "2"], 100)
X = rng.poisson(0.3, size=(n, len(genes))).astype(float)
X[labels == "0", 0:2] += rng.poisson(6, (100, 2))    # T
X[labels == "1", 2:4] += rng.poisson(6, (100, 2))    # B
X[labels == "2", 4:6] += rng.poisson(6, (100, 2))    # NK
s_phase = np.zeros(n, dtype=bool)
s_phase[200:250] = True                               # 簇2 前半 S 期
X[s_phase, 6:16] += rng.poisson(5, (50, 10))

# 双联体注入：20 个细胞 = 随机两细胞之和（写在 counts 层）
counts = X.copy()
dbl_idx = rng.choice(n, 20, replace=False)
for i in dbl_idx:
    a, b = rng.integers(0, n, 2)
    counts[i] = X[a] + X[b]
samples = np.repeat(["s1", "s2"], 150)

import anndata as ad
import scanpy as sc

ds = WS / "ds35"
ds.mkdir(exist_ok=True)
obs = pd.DataFrame({"leiden": labels, "sample": samples},
                   index=[f"c{i}" for i in range(n)])
# filtered.h5ad = counts（scrublet 输入）
ad.AnnData(X=counts, obs=obs.copy(),
           var=pd.DataFrame(index=genes)).write(ds / "filtered.h5ad")
# processed.h5ad：仿 process.py（normalize→log1p→raw=全基因→HVG 子集→scale）
adata = ad.AnnData(X=counts, obs=obs, var=pd.DataFrame(index=genes))
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
adata.raw = adata
sc.pp.highly_variable_genes(adata, n_top_genes=min(50, len(genes)),
                            flavor="seurat")
adata = adata[:, adata.var["highly_variable"]].copy()
sc.pp.scale(adata, max_value=10)
sc.pp.pca(adata, n_comps=20)
sc.pp.neighbors(adata, n_neighbors=15)
sc.tl.umap(adata)
adata.obs["leiden"] = labels
adata.write(ds / "processed.h5ad")

# meta 用 csv：sample → condition
pd.DataFrame({"condition": ["control", "treated"]},
             index=["s1", "s2"]).to_csv(DATA / "meta35.csv")


def run_script(name: str, payload: dict) -> dict:
    """monkeypatch 后子进程跑脚本（WS_ROOT/DATA_ROOT/MODEL_DIR 值绑定需补丁）。"""
    driver = (
        "import sys, pathlib; "
        "sys.path.insert(0, r'%s'); "
        "import common; "
        "common.WS_ROOT = pathlib.Path(r'%s'); "
        "common.DATA_ROOT = pathlib.Path(r'%s'); "
        "import %s as m; m.WS_ROOT = common.WS_ROOT; "
        "setattr(m, 'DATA_ROOT', common.DATA_ROOT); "
        "setattr(m, 'MODEL_DIR', pathlib.Path(r'%s')); "
        "m.run(m.main)" % (ROOT, WS, DATA, name, MODELS))
    proc = subprocess.run(
        [sys.executable, "-c", driver], input=json.dumps(payload),
        capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"], out
    return out


# 1) annotate markers 路：三簇标签正确
r1 = run_script("annotate", {
    "dataset_id": "ds35", "method": "markers",
    "marker_sets": {"T cell": ["CD3D", "CD3E"],
                    "B cell": ["MS4A1", "CD79A"],
                    "NK cell": ["NKG7", "GZMB"]}})
assert r1["cluster_assignment"] == {"0": "T cell", "1": "B cell",
                                    "2": "NK cell"}, r1["cluster_assignment"]
print("[ok] annotate markers:", r1["cluster_assignment"])

# 2) annotate celltypist 路：跑通 + 写回两列
r2 = run_script("annotate", {"dataset_id": "ds35", "method": "celltypist",
                             "model": "Immune_All_Low.pkl"})
assert r2["label_col"] == "celltypist_label" and r2["n_labels"] >= 1
print("[ok] annotate celltypist: n_labels=%d mean_conf=%.2f" % (
    r2["n_labels"], r2["mean_conf"]))

# 3) doublet：注入的双联体分数显著高于普通细胞
r3 = run_script("doublet", {"dataset_id": "ds35", "expected_rate": 0.06})
reload = ad.read_h5ad(ds / "processed.h5ad")
sc_dbl = reload.obs["doublet_score"].to_numpy(dtype=float)
assert sc_dbl[dbl_idx].mean() > np.delete(sc_dbl, dbl_idx).mean()
print("[ok] doublet: n=%d rate=%.3f (injected mean %.3f > rest %.3f)" % (
    r3["n_doublets"], r3["doublet_rate"],
    sc_dbl[dbl_idx].mean(), np.delete(sc_dbl, dbl_idx).mean()))

# 4) cellcycle：S 期细胞集中在簇 2 前半
r4 = run_script("cellcycle", {"dataset_id": "ds35"})
reload = ad.read_h5ad(ds / "processed.h5ad")
phase = reload.obs["phase"].astype(str).to_numpy()
assert (phase[s_phase] == "S").mean() > 0.5
print("[ok] cellcycle:", r4["phase_counts"])

# 5) meta 三 op：merge_csv → map_values → rename_col
r5 = run_script("meta", {"dataset_id": "ds35", "op": "merge_csv",
                         "csv_path": "meta35.csv", "key_col": "sample"})
reload = ad.read_h5ad(ds / "processed.h5ad")
assert "condition" in reload.obs and set(reload.obs["condition"].dropna()) \
    == {"control", "treated"}
r6 = run_script("meta", {"dataset_id": "ds35", "op": "map_values",
                         "col": "leiden",
                         "mapping": {"0": "T", "1": "B", "2": "NK"},
                         "out_col": "celltype"})
r7 = run_script("meta", {"dataset_id": "ds35", "op": "rename_col",
                         "old": "celltype", "new": "celltype_final"})
reload = ad.read_h5ad(ds / "processed.h5ad")
assert "celltype_final" in reload.obs and "celltype" not in reload.obs
print("[ok] meta: merge_csv/map_values/rename_col all applied")

print("SMOKE PASS: phase35 all four scripts biologically consistent")
```

注意：步骤 2/3（doublet/cellcycle/meta）需在 annotate markers 之后跑（processed.h5ad 被 annotate 改写后 obs 仍含 leiden，脚本链兼容）。

- [ ] **Step 2: 跑冒烟**

Run: `.\.venv\Scripts\python.exe code_workspace\smoke_phase35.py`
Expected: 五行 `[ok]` + `SMOKE PASS`；失败按 stderr 最小化修复并同步 sandbox 脚本，报告改动

---

### Task 9: docker 重建 + 断网容器验证

- [ ] **Step 1: 重建镜像**

Run: `docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile`
Expected: 成功（celltypist 模型预取层需联网下载 sanger 模型；scrublet 层 g++ 编译 annoy）；记录新镜像 ID

- [ ] **Step 2: 断网容器验证**

Run: `docker run --rm --network none feishu-research-agent/bio:cpu-latest python -c "import sys, pathlib; sys.path.insert(0,'/opt/sc_tools'); import celltypist, scrublet; ms = sorted(p.name for p in pathlib.Path('/opt/celltypist_models').glob('*.pkl')); import common, annotate, meta, doublet, cellcycle; print('offline ok', ms)"`
Expected: `offline ok ['Immune_All_High.pkl', 'Immune_All_Low.pkl']`

---

### Task 10: 全量回归

- [ ] **Step 1: 回归**

Run: `.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp > .pytest_last.log 2>&1; Select-String -Path .pytest_last.log -Pattern '\d+ passed' | Select-Object -Last 1`
Expected: `1041 passed`（1032 + 9），0 failed

---

### Task 11: 文档同步 + commit + 服务重启

**Files:**
- Modify: `docs/ROADMAP.md`（+Phase 35 行）
- Modify: `docs/平台功能说明书.md`（6.1 节 16→20 工具；表格 +4 行）
- Modify: `docs/使用说明书.md`（场景 D 产物表 +4 行；话术：注释/元数据/双联体/周期）
- Modify: `测试总结+2026-09-04T15-10-00.md`（+Phase 35 节；统一测试轮待办 +4 → 15 工具）
- Modify: 本计划「验证记录」节回填

- [x] **Step 1: 四文档同步**（范式仿 Phase 34；celltypist 模型预取/scrublet g++/raw=lognorm 关键事实写入测试总结教训区）

- [ ] **Step 2: commit**

```powershell
git add sandbox/sc_tools/annotate.py sandbox/sc_tools/meta.py sandbox/sc_tools/doublet.py sandbox/sc_tools/cellcycle.py sandbox/bio.Dockerfile orchestrator/tools/builtin/l3_singlecell.py tests/unit/test_l3_singlecell.py docs/superpowers/plans/2026-09-05-phase35-annotate-meta-qc.md docs/ROADMAP.md "docs/使用说明书.md" "docs/平台功能说明书.md" "测试总结+2026-09-04T15-10-00.md"
git commit -m "feat: Phase 35 注释与质控补强四件套（toolsv1 第五批）——sc_annotate 双路注释（celltypist 模型构建期预取断网可用+leiden 级 majority voting / marker 集簇投票，写回 processed 供下游 celltype_col 联动）+ sc_meta 元数据编辑（merge_csv 白名单挂载/map_values/rename_col）+ sc_doublet（scrublet，counts 走 filtered 链按 obs_names 写回，annoy g++ 同层编译先例复用）+ sc_cellcycle（Tirosh S/G2M 内嵌离线）；本机冒烟生物学自洽 + 断网容器验证 ok；TDD 9 用例回归 1041 全过；真机验收延后统一测试轮（累计 15 工具），双说明书/ROADMAP/测试总结同步"
```

- [ ] **Step 3: 服务重启对齐 HEAD**

```powershell
$old = Get-Content .ws_client.pid; Stop-Process -Id $old -Force -ErrorAction SilentlyContinue
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

Expected: `[PASS] ws_client 已启动：pid=xxxx HEAD=<新commit>`；err.log 含 Lark connected

---

## 验证记录

### 冒烟输出（宿主假数据，五行）

1. markers 路：三簇投票全对（T/B/NK）；
2. celltypist 路：跑通 n_labels=3；
3. doublet：注入双联体组均分 0.174 > 其余 0.039（n=7，rate=0.023）；
4. cellcycle：S 期富集断言通过；
5. meta：三 op（merge_csv/map_values/rename_col）链式写回正确。

### 镜像

342ef18a738f → **83369d55f9c5**（celltypist 模型预取层：清华源装
1.7.1 + 模型清单 61 个、Immune_All_Low/High.pkl 落
/opt/celltypist_models/；scrublet g++ 同层编译层：annoy 编译后 purge）。
断网容器（--network none）验证：四新模块 + celltypist/scrublet 全部可
导入，模型清单 `offline ok ['Immune_All_High.pkl',
'Immune_All_Low.pkl']`，annotate 迷你冒烟（50 细胞，majority_voting +
over_clustering）`annotate ok (50, 1)`。

### 回归

单测 **43 passed**（34+9）；全量 **1041 passed**（1032+9），55.33s，
0 failed。

### sandbox 修复记录（实施期）

1. **annotate conf_score API**：celltypist 1.7.1 的 predicted_labels 无
   conf_score 列 → 改 `pred.to_adata(insert_conf_by='majority_voting')`
   取置信度。
2. **doublet n_prin_comps 重试**：scrublet 内部先按基因变异过滤再 PCA，
   小数据 n_prin_comps 越界 → doublet.py 加 n_prin_comps 减半重试循环
   （下限 2）。

附：冒烟假基因名与模型零交集时 celltypist 直接抛
`ValueError: No features overlap with the model`（部分交集才补零警告），
验证命令须混入真实免疫 marker——属验证数据问题，镜像/Dockerfile 无改动。

### commit hash：29e2d43


