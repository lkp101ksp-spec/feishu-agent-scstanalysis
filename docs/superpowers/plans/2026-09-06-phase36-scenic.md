# Phase 36 sc_scenic 调控网络（pySCENIC）实施计划（spec+plan 合并）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 sc_scenic 转录调控网络工具（pySCENIC 三幕：GRNBoost2→cisTarget→AUCell），sc_* 工具数 20→21。

**Architecture:** 沿用 BioRunner 约定。数据库**运行时只读挂载**（不进镜像）：handler 挂 `{db_root}/cisTarget_databases→/scenic_db/cistarget`、`{db_root}/motifAnnotations→/scenic_db/motifannot`；TF 列表从 motif tbl 的 gene_name 派生。产物落 `WS/{id}/scenic/`，不写回 processed.h5ad（抽样子集），AUC 可用 sc_meta merge_csv 回挂。

**Tech Stack:** pyscenic 0.12.1 + arboreto 0.1.6 + ctxcore 0.2.0 + dask 2026.8.0（宿主实测通过）。

**宿主实测结论（probe_pyscenic.py，必须逐条落实）**：
1. **锁 `setuptools<81`**（84+ 删 pkg_resources，ctxcore import 炸；宿主已手动降 80.9.0）
2. **grnboost2() 与 dask-expr 不兼容** → 改 `arboreto.core.create_graph(include_meta=True)` + `client.compute(sync=True)`（见脚本 `_grn()`）
3. **prune2df 把 generator 传给新版 from_delayed** → monkeypatch `pyscenic.prune.from_delayed` 先物化 list
4. **pyscenic/transform.py 等引用 np.object/np.float**（numpy≥1.24 已删）→ numpy 别名 shim；容器内经 site-packages/sitecustomize.py 覆盖 dask worker 子进程（宿主冒烟由 scenic.py 模块顶部 shim + 进程内 dask 覆盖，已实测可行）
5. **禁用 custom_multiprocessing**（Py3.12 spawn 不兼容且会挂死父进程）；dask LocalCluster 走 loopback，--network none 可用（断网验证会实测）
6. 容器镜像**不变更**数据库层（挂载方案）；Dockerfile 只加 pip 层 + sitecustomize COPY
7. pytest 加 `--basetemp=.pytest_tmp`；回归落 `.pytest_last.log`；venv = `.\.venv\Scripts\python.exe`
8. 冒烟 monkeypatch：值绑定需双补丁（common 与 scenic 模块的 WS_ROOT/SCENIC 目录常量）

---

### Task 1: scenic.py 容器脚本

**Files:**
- Create: `sandbox/sc_tools/scenic.py`

- [ ] **Step 1: 写脚本**

```python
"""sc_scenic：转录调控网络推断（Phase 36，pySCENIC；对齐 scop RunSCENIC）。

stdin: {"dataset_id": ..., "species": "human"|"mouse",
        "db": "500bp"|"10kb"|"both", "max_cells": 3000,
        "celltype_col": "leiden", "n_workers": 2, "seed": 42}
counts 走 filtered/raw 链（基因过滤：count>=3 且 >=3 细胞；按簇分层抽样
max_cells）。TF 列表从 motif 注释 tbl 的 gene_name 派生（hg38 ~1839）。
三幕：GRNBoost2（arboreto create_graph 绕 dask-expr 不兼容）→
cisTarget motif 剪枝（from_delayed monkeypatch 物化 generator）→
AUCell。产物落 scenic/：adjacencies.csv / regulons.json /
regulon_auc.csv / rss.csv / scenic_heatmap.png。n_regulons=0 不报错
（小样本/低命中率正常）。不写回 processed.h5ad（抽样是子集），AUC
可用 sc_meta merge_csv 回挂。

兼容性三件套（宿主 probe 实测）：
1) setuptools<81（构建层锁定，ctxcore 依赖 pkg_resources）
2) grnboost2 走 create_graph(include_meta=True) + client.compute
3) prune2df 的 from_delayed monkeypatch + numpy 别名 shim
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

# numpy>=1.24 移除的别名 shim（pyscenic 0.12.1 transform/diptest/rss 仍引用）；
# 须在 import pyscenic 任何子模块之前执行。容器内另有 site-packages/
# sitecustomize.py 覆盖 dask worker 子进程。
for _alias, _typ in {"object": object, "float": float, "int": int,
                     "str": str}.items():
    if not hasattr(np, _alias):
        setattr(np, _alias, _typ)

from common import WS_ROOT, emit, load_adata, read_args, run

# 容器内由 handler 挂载：{db_root}/cisTarget_databases → CISTARGET_DIR，
# {db_root}/motifAnnotations → MOTIF_DIR（均只读）。冒烟可 patch。
CISTARGET_DIR = Path("/scenic_db/cistarget")
MOTIF_DIR = Path("/scenic_db/motifannot")

_SPECIES = {"human": "hg38", "mouse": "mm10"}
_DB_FILES = {
    "500bp": "{genome}__refseq-r80__500bp_up_and_100bp_down_tss.mc9nr."
             "genes_vs_motifs.rankings.feather",
    "10kb": "{genome}__refseq-r80__10kb_up_and_down_tss.mc9nr."
            "genes_vs_motifs.rankings.feather",
}
_MOTIF_TBL = {"hg38": "motifs-v9-nr.hgnc-m0.001-o0.0.tbl",
              "mm10": "motifs-v9-nr.mgi-m0.001-o0.0.tbl"}


def _db_paths(genome: str, db: str) -> list[Path]:
    """按 species/db 参数解析 rankings feather 路径列表。"""
    keys = ["500bp", "10kb"] if db == "both" else [db]
    paths = [CISTARGET_DIR / genome / _DB_FILES[k].format(genome=genome)
             for k in keys]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        avail = sorted(p.name for p in (CISTARGET_DIR / genome).glob("*"))
        raise ValueError(f"rankings db missing: {missing}; "
                         f"available under {genome}: {avail}")
    return paths


def _grn(expr: pd.DataFrame, tfs: list[str], n_workers: int,
         seed: int) -> pd.DataFrame:
    """GRNBoost2 共表达：arboreto 0.1.6 的 grnboost2() 与 dask-expr 不兼容
    （空 meta 列表炸 from_delayed），直接 create_graph(include_meta=True)
    + client.compute 绕路。LocalCluster 走 loopback，--network none 可用。
    禁用 custom_multiprocessing（Py3.12 spawn 不兼容且挂死父进程）。"""
    from arboreto.algo import _prepare_input
    from arboreto.core import SGBM_KWARGS, create_graph
    from dask.distributed import Client, LocalCluster

    cluster = LocalCluster(n_workers=n_workers, threads_per_worker=1,
                           processes=True, dashboard_address=None,
                           silence_logs=30)
    client = Client(cluster)
    try:
        matrix, gene_names, tf_names = _prepare_input(expr, None, tfs)
        links_graph, _meta_graph = create_graph(
            matrix, gene_names, tf_names, "GBM", SGBM_KWARGS, client,
            include_meta=True, seed=seed)
        adj = client.compute(links_graph, sync=True).sort_values(
            by="importance", ascending=False)
    finally:
        client.close()
        cluster.close()
    return adj


def _prune(dbs, modules, motif_tbl: Path, n_workers: int) -> pd.DataFrame:
    """cisTarget motif 剪枝：prune2df 把 generator 传给新版 dask
    from_delayed（无 len）→ monkeypatch 先物化 list。"""
    import pyscenic.prune as prune_mod
    from pyscenic.prune import prune2df

    _orig_fd = prune_mod.from_delayed

    def _fd_compat(dfs, *args, **kwargs):
        """dask-expr 的 from_delayed 不接受 generator，先物化。"""
        if not isinstance(dfs, (list, tuple)):
            dfs = list(dfs)
        return _orig_fd(dfs, *args, **kwargs)

    prune_mod.from_delayed = _fd_compat
    return prune2df(dbs, modules, str(motif_tbl), num_workers=n_workers)


def _subsample_cells(labels: pd.Series, max_cells: int,
                     seed: int) -> np.ndarray:
    """按簇分层抽样（小簇全保留，大簇按比例），返回选中 cell 索引数组。"""
    if len(labels) <= max_cells:
        return np.arange(len(labels))
    rng = np.random.default_rng(seed)
    frac = labels.value_counts(normalize=True)
    take = (frac * max_cells).clip(lower=5).astype(int)
    idx_parts = []
    for grp, k in take.items():
        pos = np.flatnonzero((labels == grp).to_numpy())
        k = min(k, len(pos))
        idx_parts.append(rng.choice(pos, k, replace=False))
    return np.sort(np.concatenate(idx_parts))


def main() -> None:
    """主流程：抽样+过滤 → GRN → 剪枝 → AUCell → RSS → 产物落盘。"""
    args = read_args()
    species = str(args.get("species", "human")).strip().lower()
    db = str(args.get("db", "500bp")).strip()
    max_cells = int(args.get("max_cells", 3000))
    celltype_col = str(args.get("celltype_col", "leiden")).strip()
    n_workers = max(1, int(args.get("n_workers", 2)))
    seed = int(args.get("seed", 42))

    if species not in _SPECIES:
        raise ValueError(f"species must be human or mouse, got {species!r}")
    if db not in ("500bp", "10kb", "both"):
        raise ValueError(f"db must be 500bp/10kb/both, got {db!r}")
    genome = _SPECIES[species]
    db_paths = _db_paths(genome, db)
    motif_tbl = MOTIF_DIR / genome / _MOTIF_TBL[genome]
    if not motif_tbl.exists():
        raise ValueError(f"motif annotation tbl missing: {motif_tbl}")

    # TF 列表从 motif 注释 tbl 派生（gene_name 去重）
    tfs = pd.read_csv(motif_tbl, sep="\t",
                      usecols=["gene_name"])["gene_name"].unique().tolist()

    counts_ad = load_adata({"dataset_id": args["dataset_id"], "file": "any"})
    proc = load_adata({"dataset_id": args["dataset_id"],
                       "file": "processed"})
    if celltype_col not in proc.obs:
        raise ValueError(f"celltype column {celltype_col!r} not in "
                         f"processed obs")
    common_cells = counts_ad.obs_names.intersection(proc.obs_names)
    labels_all = proc.obs.loc[common_cells, celltype_col].astype(str)
    idx = _subsample_cells(labels_all, max_cells, seed)
    cells = common_cells[idx]

    sub = counts_ad[cells]
    import scipy.sparse as sp
    X = sub.X
    ge3 = (X >= 3).sum(axis=0)
    keep = np.asarray(ge3 >= 3).ravel() if sp.issparse(X) \
        else np.asarray(ge3).ravel() >= 3
    genes = sub.var_names[keep]
    if len(genes) < 50:
        raise ValueError(f"too few genes after filtering: {len(genes)} (<50)")
    Xd = sub[:, genes].X
    Xd = Xd.toarray() if sp.issparse(Xd) else np.asarray(Xd)
    expr = pd.DataFrame(Xd, index=cells, columns=genes.astype(str))
    labels = labels_all.loc[cells]

    t0 = time.time()
    adj = _grn(expr, tfs, n_workers, seed)
    t_grn = time.time() - t0

    from ctxcore.rnkdb import FeatherRankingDatabase
    dbs = [FeatherRankingDatabase(str(p), name=p.stem) for p in db_paths]
    db_genes = set(dbs[0].genes)
    expr_db = expr.loc[:, [g for g in expr.columns if g in db_genes]]
    adj_db = adj[adj["target"].isin(expr_db.columns)
                 & adj["TF"].isin(expr_db.columns)]

    from pyscenic.utils import modules_from_adjacencies
    modules = list(modules_from_adjacencies(adj_db, expr_db))

    t1 = time.time()
    df = _prune(dbs, modules, motif_tbl, n_workers)
    t_ctx = time.time() - t1

    from pyscenic.prune import df2regulons
    regulons = df2regulons(df) if not df.empty else []

    out_dir = WS_ROOT / args["dataset_id"] / "scenic"
    out_dir.mkdir(parents=True, exist_ok=True)
    adj_csv = out_dir / "adjacencies.csv"
    adj.to_csv(adj_csv, index=False)
    df.to_csv(out_dir / "regulons_enriched.csv", index=False)
    regulons_json = out_dir / "regulons.json"
    regulons_json.write_text(json.dumps(
        {r.name: sorted(r.genes) for r in regulons}, ensure_ascii=False),
        encoding="utf-8")

    result: dict = {
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "species": species, "db": db,
        "n_cells_used": int(len(cells)),
        "n_genes": int(len(genes)),
        "n_tfs": int(len(tfs)),
        "n_adjacencies": int(len(adj)),
        "n_modules": int(len(modules)),
        "n_regulons": int(len(regulons)),
        "adj_csv": str(adj_csv),
        "regulons_json": str(regulons_json),
        "elapsed_sec": {"grn": round(t_grn, 1), "ctx": round(t_ctx, 1)},
    }

    if not regulons:
        result["note"] = ("0 regulons：小样本/motif 命中低属正常；"
                          "adjacencies 共表达结果仍可用，或试 db=10kb、"
                          "增大 max_cells")
        emit(result)
        return

    t2 = time.time()
    from pyscenic.aucell import aucell
    auc = aucell(expr_db, regulons, num_workers=n_workers, seed=seed)
    auc.index = auc.index.astype(str)
    auc_csv = out_dir / "regulon_auc.csv"
    auc.to_csv(auc_csv)

    from pyscenic.rss import regulon_specificity_scores
    rss = regulon_specificity_scores(auc, labels)
    rss_csv = out_dir / "rss.csv"
    rss.to_csv(rss_csv, index_label="regulon")

    # 簇均值 z-score 热图（按各簇 RSS 最高的 regulon 取并集 top30）
    auc_cluster = auc.groupby(labels).mean()
    top_regs = []
    for c in rss.columns:
        top_regs.extend(rss[c].nlargest(5).index.tolist())
    top_regs = list(dict.fromkeys(top_regs))[:30]
    M = auc_cluster[top_regs].T
    Mz = M.sub(M.mean(axis=1), axis=0).div(
        M.std(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(max(6, 0.5 * Mz.shape[1] + 3),
                                    max(4, 0.22 * Mz.shape[0] + 2)))
    im = ax.imshow(Mz.to_numpy(), aspect="auto", cmap="RdBu_r",
                   vmin=-2, vmax=2)
    ax.set_xticks(range(Mz.shape[1]))
    ax.set_xticklabels(Mz.columns, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(Mz.shape[0]))
    ax.set_yticklabels(Mz.index, fontsize=6)
    fig.colorbar(im, ax=ax, shrink=0.7, label="cluster mean AUC (z)")
    ax.set_title(f"SCENIC regulons ({species}, {db})", fontsize=10)
    fig.tight_layout()
    png_path = out_dir / "scenic_heatmap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    result.update({
        "top_regulons_by_cluster": {
            str(c): [str(r) for r in rss[c].nlargest(3).index]
            for c in rss.columns},
        "auc_csv": str(auc_csv),
        "rss_csv": str(rss_csv),
        "heatmap_png": str(png_path),
        "note": "AUC 为抽样子集；可用 sc_meta op=merge_csv 按细胞名回挂 "
                "processed.h5ad",
    })
    result["elapsed_sec"]["aucell"] = round(time.time() - t2, 1)
    emit(result)


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check sandbox/sc_tools/scenic.py`
Expected: 零告警

---

### Task 2: numpy shim sitecustomize + Dockerfile pyscenic 层

**Files:**
- Create: `sandbox/scenic_site/sitecustomize.py`
- Modify: `sandbox/bio.Dockerfile`（scrublet 层后追加）

- [ ] **Step 1: sitecustomize.py**

```python
"""numpy>=1.24 移除的别名 shim（pyscenic 0.12.1 transform/diptest/rss 仍引用
np.object/np.float）。

置于 site-packages 由 site 模块自动导入——dask worker 子进程（spawn 出的
全新解释器）也会被覆盖，这是 scenic.py 进程内 shim 之外的兜底。"""
import numpy as np

for _alias, _typ in {"object": object, "float": float, "int": int,
                     "str": str}.items():
    if not hasattr(np, _alias):
        setattr(np, _alias, _typ)
```

- [ ] **Step 2: Dockerfile 追加（scrublet 层之后、非 root 用户注释之前）**

```dockerfile
# Phase 36 调控网络：pyscenic 0.12.1。锁 setuptools<81（84+ 删了
# pkg_resources，ctxcore import 即炸）；sitecustomize 补 numpy>=1.24
# 移除的别名（np.object/np.float），放 site-packages 由 site 自动导入
# 以覆盖 dask worker 子进程。宿主 probe 实测：pandas 2.3.3/numpy 2.5.2/
# dask 2026.8.0 下三幕全绿（GRNBoost2 走 create_graph 绕路、prune2df
# from_delayed 物化 monkeypatch——均在 scenic.py 内）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pyscenic "setuptools<81"
COPY scenic_site/sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
```

- [ ] **Step 3: 宿主 venv 持久化 setuptools 锁（probe 已手动降过，幂等执行）**

Run: `.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "setuptools<81"`
Expected: setuptools 80.x satisfied

---

### Task 3: 注册 + settings/app.py 接线

**Files:**
- Modify: `config/settings.py`（bio 段加字段 + 工厂函数加 env 读取）
- Modify: `orchestrator/app.py`（register_l3_singlecell 调用加参）
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`（docstring；import Path；注册函数签名；handler + ToolSpec 追加在 sc_cellcycle 之后）

- [ ] **Step 1: settings.py**

bio 段（`bio_memory` 行之后）加：

```python
    bio_memory: str = "16g"
    # Phase 36：SCENIC 数据库根（cisTarget_databases/ 与 motifAnnotations/
    # 所在目录）；空 = 仓库根。容器内只读挂载到 /scenic_db/。
    bio_scenic_db_root: str = ""
```

工厂函数（`bio_memory=os.environ.get("BIO_MEMORY", "16g"),` 之后）加：

```python
        bio_scenic_db_root=os.environ.get("BIO_SCENIC_DB_ROOT", ""),
```

- [ ] **Step 2: app.py**

`register_l3_singlecell(` 调用块改为：

```python
                register_l3_singlecell(
                    self.registry, bio_runner,
                    bio_use_gpu=settings.bio_use_gpu,
                    bio_gpu_image=settings.bio_gpu_image,
                    bio_scenic_db_root=settings.bio_scenic_db_root,
                )
```

- [ ] **Step 3: l3_singlecell.py**

1. docstring：`Phase 20/31/32/33/34/35` → `Phase 20/31/32/33/34/35/36`，`20 个` → `21 个`
2. import 区加 `from pathlib import Path`
3. 注册函数签名加参（`bio_gpu_image` 之后）：

```python
    bio_gpu_image: str = "feishu-research-agent/bio:gpu-latest",
    bio_scenic_db_root: str = "",
) -> None:
```

4. handler（追加在 sc_cellcycle handler 之后）：

```python
    def sc_scenic(*, dataset_ref: str, species: str = "human",
                  db: str = "500bp", max_cells: int = 3000,
                  celltype_col: str = "leiden", n_workers: int = 2,
                  seed: int = 42) -> dict:
        """转录调控网络（Phase 36，pySCENIC）：DB 目录只读挂载进容器。"""
        db_root = (Path(bio_scenic_db_root) if bio_scenic_db_root
                   else Path(__file__).resolve().parents[3])
        ct_dir = db_root / "cisTarget_databases"
        ma_dir = db_root / "motifAnnotations"
        if not ct_dir.is_dir() or not ma_dir.is_dir():
            return {
                "error_code": "SC_CONFIG",
                "error_message": (
                    f"SCENIC db not found: expect {ct_dir} and {ma_dir}; "
                    "set BIO_SCENIC_DB_ROOT to the directory containing "
                    "cisTarget_databases/ and motifAnnotations/"),
            }
        try:
            out = runner.run("scenic", {
                "dataset_id": dataset_ref, "species": species, "db": db,
                "max_cells": max_cells, "celltype_col": celltype_col,
                "n_workers": n_workers, "seed": seed,
            }, mounts=[(str(ct_dir), "/scenic_db/cistarget"),
                       (str(ma_dir), "/scenic_db/motifannot")],
                timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

5. ToolSpec（追加在 sc_cellcycle ToolSpec 之后）：

```python
    registry.register(ToolSpec(
        name="sc_scenic",
        description=(
            "转录调控网络推断（Phase 36，pySCENIC 三幕）：GRNBoost2 共表达"
            "→ cisTarget motif 剪枝 → AUCell 打分，回答\"各细胞类型的核心"
            "转录因子/regulon 是什么、活性多高\"。species 选 human/mouse，"
            "db 选 500bp（快）/10kb（更多 regulon）/both。默认按簇分层抽样"
            " 3000 细胞（大计算量护栏）。产物：adjacencies 共表达表、"
            "regulons 列表、regulon_auc.csv（抽样细胞×regulon 活性）、"
            "rss.csv（簇特异 regulon 排序）、热图 png。n_regulons=0 不算"
            "失败（小样本/motif 命中低正常，note 会说明）。需先跑 "
            "sc_process。耗时较长（分钟~小时级）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "species": {"type": "string", "default": "human",
                            "enum": ["human", "mouse"]},
                "db": {"type": "string", "default": "500bp",
                       "enum": ["500bp", "10kb", "both"],
                       "description": "cisTarget rankings 库（500bp 快，"
                                      "10kb 捕获更多 regulon）"},
                "max_cells": {"type": "integer", "default": 3000,
                              "description": "分层抽样上限（GRNBoost2 是"
                                             "计算重头）"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "簇标签列（抽样/RSS 用）"},
                "n_workers": {"type": "integer", "default": 2,
                              "description": "dask worker 数（内存随 worker "
                                             "线性涨，16g 容器勿超 4）"},
                "seed": {"type": "integer", "default": 42},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_scenic,
        timeout_sec=3600,
    ))
```

- [ ] **Step 4: ruff 检查**

Run: `.\.venv\Scripts\python.exe -m ruff check config/settings.py orchestrator/app.py orchestrator/tools/builtin/l3_singlecell.py`
Expected: 零告警

---

### Task 4: 单元测试（4 用例）

**Files:**
- Modify: `tests/unit/test_l3_singlecell.py`（文件尾追加）

- [ ] **Step 1: 追加 4 个用例**

```python
# === Phase 36：调控网络（scenic） ===


def test_sc_scenic_registered_l1(tmp_path):
    """sc_scenic 注册可见、L1_compute、timeout 3600。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    assert "sc_scenic" in names
    spec = reg.get("sc_scenic")
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 3600


def test_sc_scenic_forwards_and_mounts(tmp_path):
    """db 目录存在时转发参数并只读挂载两个 DB 目录。"""
    from orchestrator.tools.builtin.l3_singlecell import (
        register_l3_singlecell,
    )
    (tmp_path / "cisTarget_databases").mkdir()
    (tmp_path / "motifAnnotations").mkdir()
    reg = ToolRegistry()
    runner = SimpleNamespace(
        run=MagicMock(), resolve_data_path=MagicMock())
    register_l3_singlecell(reg, runner, bio_scenic_db_root=str(tmp_path))
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "species": "human",
        "n_regulons": 12, "top_regulons_by_cluster": {"0": ["SPI1(+)"]}}
    out = reg.get("sc_scenic").handler(dataset_ref="d", species="human",
                                       db="10kb", max_cells=1500)
    call = runner.run.call_args
    assert call.args[0] == "scenic"
    assert call.args[1]["db"] == "10kb"
    assert call.args[1]["max_cells"] == 1500
    mount_targets = sorted(m[1] for m in call.kwargs["mounts"])
    assert mount_targets == ["/scenic_db/cistarget", "/scenic_db/motifannot"]
    assert call.kwargs["timeout_sec"] == 3600
    assert "ok" not in out
    assert out["n_regulons"] == 12


def test_sc_scenic_config_error_when_db_missing(tmp_path):
    """DB 目录缺失时不进容器，直接 SC_CONFIG 错误。"""
    from orchestrator.tools.builtin.l3_singlecell import (
        register_l3_singlecell,
    )
    reg = ToolRegistry()
    runner = _runner(tmp_path)
    register_l3_singlecell(reg, runner, bio_scenic_db_root=str(tmp_path))
    out = reg.get("sc_scenic").handler(dataset_ref="d")
    assert out["error_code"] == "SC_CONFIG"
    runner.run.assert_not_called()


def test_sc_scenic_schema_enums(tmp_path):
    """species/db enum 锁定。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_scenic").parameters["properties"]
    assert props["species"]["enum"] == ["human", "mouse"]
    assert props["db"]["enum"] == ["500bp", "10kb", "both"]
```

注意：测试文件头部若缺 `ToolRegistry` import 需补（读文件头确认现有 import 后再插）。

- [ ] **Step 2: 跑测试 + ruff**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_l3_singlecell.py -q --basetemp=.pytest_tmp`
Expected: `47 passed`（43 + 4）

Run: `.\.venv\Scripts\python.exe -m ruff check tests/unit/test_l3_singlecell.py`
Expected: 零告警

---

### Task 5: 本机冒烟（真实库 + 注入模块）

**Files:**
- Create: `code_workspace/smoke_phase36.py`（untracked）

- [ ] **Step 1: 写冒烟脚本**

```python
"""Phase 36 scenic.py 本机冒烟（宿主 venv，真实 hg38 500bp 库）。

假数据：200 细胞 × 300 基因（真实免疫符号 + SPI1 与 9 个靶基因注入相关
表达；3 簇）。全链路：filtered.h5ad(counts) + processed.h5ad(leiden)。
生物学断言：adjacencies 中 SPI1 的 top-20 靶基因与注入集交集 >=3；
regulons>=0（0 允许但不打断）；regulons>0 时 AUC 行数=细胞数。
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"i:\飞书agent\sandbox\sc_tools")
WS = Path(r"i:\飞书agent\code_workspace\smoke_ws36")
CT = Path(r"i:\飞书agent\cisTarget_databases")
MA = Path(r"i:\飞书agent\motifAnnotations")
WS.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(7)
REAL = ["CD3D", "CD3E", "MS4A1", "CD79A", "NKG7", "GZMB", "CD14", "LST1",
        "S100A8", "FCGR3A", "GNLY", "PTPRC", "IL7R", "LTB", "MAL", "CD8A",
        "CCR7", "GZMK", "CCL5", "CST7", "PRF1", "KLRD1", "FCER1G",
        "TYROBP", "CTSS", "S100A9", "VCAN", "LYZ", "HLA-DRA", "CEBPA",
        "GATA1", "IRF8", "STAT1", "STAT3", "JUN", "FOS", "EGR1", "MYC",
        "ETS1", "RUNX1", "FLI1", "KLF4"]
SPI1_TG = ["CD14", "LST1", "S100A8", "FCER1G", "TYROBP", "CTSS",
           "S100A9", "VCAN", "LYZ"]
genes = REAL + [f"G{i:03d}" for i in range(300 - len(REAL))]
n = 200
labels = np.repeat(["0", "1", "2"], [80, 60, 60])
counts = rng.poisson(1.5, size=(n, len(genes))).astype(float)
spi1 = rng.poisson(3.0, n).astype(float)
counts[labels == "2", genes.index("SPI1")] += spi1[labels == "2"]
for tg in SPI1_TG:
    j = genes.index(tg)
    counts[:, j] += spi1 + rng.poisson(0.8, n)

import anndata as ad
import scanpy as sc

ds = WS / "ds36"
ds.mkdir(exist_ok=True)
obs = pd.DataFrame({"leiden": labels}, index=[f"c{i}" for i in range(n)])
ad.AnnData(X=counts, obs=obs.copy(),
           var=pd.DataFrame(index=genes)).write(ds / "filtered.h5ad")
adata = ad.AnnData(X=counts, obs=obs, var=pd.DataFrame(index=genes))
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
adata.raw = adata
sc.pp.highly_variable_genes(adata, n_top_genes=200, flavor="seurat")
adata = adata[:, adata.var["highly_variable"]].copy()
sc.pp.scale(adata, max_value=10)
sc.pp.pca(adata, n_comps=20)
sc.pp.neighbors(adata, n_neighbors=15)
sc.tl.umap(adata)
adata.obs["leiden"] = labels
adata.write(ds / "processed.h5ad")


def run_script(payload: dict) -> dict:
    """monkeypatch WS_ROOT + 两个 DB 目录常量后子进程跑 scenic.py。"""
    driver = (
        "import sys, pathlib; "
        "sys.path.insert(0, r'%s'); "
        "import common; common.WS_ROOT = pathlib.Path(r'%s'); "
        "import scenic as m; m.WS_ROOT = common.WS_ROOT; "
        "m.CISTARGET_DIR = pathlib.Path(r'%s'); "
        "m.MOTIF_DIR = pathlib.Path(r'%s'); "
        "m.run(m.main)" % (ROOT, WS, CT, MA))
    proc = subprocess.run(
        [sys.executable, "-c", driver], input=json.dumps(payload),
        capture_output=True, text=True, timeout=3600)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"], out
    return out


r = run_script({"dataset_id": "ds36", "species": "human", "db": "500bp",
                "max_cells": 3000})
print("[scenic]", {k: r[k] for k in ("n_cells_used", "n_genes", "n_tfs",
                                     "n_adjacencies", "n_modules",
                                     "n_regulons", "elapsed_sec")})
adj = pd.read_csv(r["adj_csv"])
top = adj[adj["TF"] == "SPI1"].nlargest(20, "importance")
overlap = set(top["target"]) & set(SPI1_TG)
assert len(overlap) >= 3, f"SPI1 top-20 overlap only {overlap}"
print("[ok] SPI1 module recovered, overlap =", sorted(overlap))
if r["n_regulons"] > 0:
    auc = pd.read_csv(r["auc_csv"], index_col=0)
    assert auc.shape[0] == r["n_cells_used"]
    print("[ok] aucell:", auc.shape, "top:", r["top_regulons_by_cluster"])
else:
    print("[note] 0 regulons（允许）:", r["note"][:80])
print("SMOKE PASS: phase36 scenic pipeline end-to-end")
```

- [ ] **Step 2: 跑冒烟**

Run: `.\.venv\Scripts\python.exe code_workspace\smoke_phase36.py`
Expected: `[scenic]` 统计行 + `[ok] SPI1 module recovered` + `SMOKE PASS`（200 细胞×300 基因规模预计 1-3 分钟；prune 阶段占大头）。失败按 stderr 最小化修复并同步 sandbox 脚本，报告改动

---

### Task 6: docker 重建 + 断网容器集成验证

- [ ] **Step 1: 重建镜像**

Run: `docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile`
Expected: 成功；记录新镜像 ID

- [ ] **Step 2: 断网容器 import + sitecustomize 生效验证**

Run: `docker run --rm --network none feishu-research-agent/bio:cpu-latest python -c "import sys, numpy as np; sys.path.insert(0,'/opt/sc_tools'); print('np.object shim:', np.object is object); import pyscenic, ctxcore, arboreto; import common, scenic; print('offline import ok', pyscenic.__version__)"`
Expected: `np.object shim: True` + `offline import ok 0.12.1`（sitecustomize 生效证明 worker 子进程也会被覆盖）

- [ ] **Step 3: 断网容器带挂载真跑 scenic.py（集成终验）**

```powershell
docker run --rm -i --network none --cpus 4 --memory 16g `
  -v i:\飞书agent\code_workspace\smoke_ws36:/ws `
  -v i:\飞书agent\cisTarget_databases:/scenic_db/cistarget:ro `
  -v i:\飞书agent\motifAnnotations:/scenic_db/motifannot:ro `
  feishu-research-agent/bio:cpu-latest python /opt/sc_tools/scenic.py
```
stdin: `{"dataset_id": "ds36", "species": "human", "db": "500bp", "max_cells": 3000}`
Expected: 末行 JSON `ok: true`，n_regulons 与宿主冒烟同量级；**重点验证 dask LocalCluster 在 --network none 下经 loopback 正常工作**（若 loopback 被禁导致 dask 起不来，报错全文报告）

---

### Task 7: 全量回归

Run: `.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp > .pytest_last.log 2>&1; Select-String -Path .pytest_last.log -Pattern '\d+ passed' | Select-Object -Last 1`
Expected: `1045 passed`（1041 + 4），0 failed

---

### Task 8: 文档同步 + commit + 服务重启

**Files:**
- Modify: `docs/ROADMAP.md`（+Phase 36 行）
- Modify: `docs/平台功能说明书.md`（6.1 节 20→21 工具；表格 +sc_scenic 行）
- Modify: `docs/使用说明书.md`（场景 D 产物表 +1 行；话术："帮我找各细胞类型的核心转录因子/调控网络"）
- Modify: `测试总结+2026-09-06T00-58-47.md`（probe 已建此文件；追加 Phase 36 实施节：四坑绕法、冒烟结果、镜像 ID、回归统计、统一测试轮待办 16 工具）
- Modify: 本计划「验证记录」节回填

- [ ] **Step 1: 五文档同步**（范式仿 Phase 35；pyscenic 四坑与 setuptools<81 锁、运行时挂载设计写入测试总结教训区）

- [ ] **Step 2: commit**

```powershell
git add sandbox/sc_tools/scenic.py sandbox/scenic_site/sitecustomize.py sandbox/bio.Dockerfile config/settings.py orchestrator/app.py orchestrator/tools/builtin/l3_singlecell.py tests/unit/test_l3_singlecell.py docs/superpowers/plans/2026-09-06-phase36-scenic.md docs/ROADMAP.md "docs/使用说明书.md" "docs/平台功能说明书.md" "测试总结+2026-09-06T00-58-47.md"
git commit -m "feat: Phase 36 sc_scenic 转录调控网络（pySCENIC 三幕：GRNBoost2 走 create_graph 绕 dask-expr 不兼容 + cisTarget 剪枝 from_delayed 物化 monkeypatch + AUCell；DB 运行时只读挂载不进镜像，TF 从 motif tbl 派生，species 人/鼠 + db 500bp/10kb/both；numpy 别名 shim 经 site-packages sitecustomize 覆盖 dask worker 子进程，setuptools<81 锁定——宿主 probe 实测四坑全绕）+ settings BIO_SCENIC_DB_ROOT + app 接线；冒烟 SPI1 注入模块回收 + 断网容器带挂载集成跑通；TDD 4 用例，回归 1045 全过；真机验收延后统一测试轮（累计 16 工具），双说明书/ROADMAP/测试总结同步"
```

- [ ] **Step 3: 服务重启对齐 HEAD**（同 Phase 35：Stop-Process 旧 pid → start.ps1 → err.log 验 Lark connected）

---

## 验证记录

**Task 1-4 实施 + TDD**
- scenic.py / sitecustomize.py / Dockerfile 层 / settings `bio_scenic_db_root`（env BIO_SCENIC_DB_ROOT，空=仓库根）/ app.py 接线 / l3_singlecell handler+ToolSpec 全部落地；ruff 各文件零告警。
- 单测：**47 passed**（43+4：注册可见/转发+双挂载断言/DB 缺失 SC_CONFIG/schema enum）。

**Task 5 本机冒烟（宿主 venv，真实 hg38 500bp 库，200 细胞×300 基因 + SPI1 注入模块）**
- 输出：`n_cells_used=200, n_genes=300, n_tfs=1839, n_adjacencies=4186, n_modules=8, n_regulons=2 (CEBPA(+), SPI1(+))`；SPI1 top-20 回收 **6/9**（CTSS/FCER1G/LYZ/S100A8/S100A9/TYROBP）；AUC (200,2)。
- 耗时：grn 12.0s / ctx 16.0s / aucell 2.2s。SMOKE PASS。
- 冒烟期 sandbox 修复两处（同步进 scenic.py）：① 第五坑——prune2df 默认 dask_multiprocessing spawn 子进程不继承进程内 numpy shim → `_prune` 改自建线程式 `LocalCluster(processes=False)` + 传 Client；② RSS 方向——pyscenic 0.12.1 `regulon_specificity_scores` 返回 行=簇、列=regulon，改按 rss.index 迭代簇。（冒烟脚本自身另修 REAL 列表漏 SPI1 + 断言附 stderr。）

**Task 6 docker 重建 + 断网容器集成**
- 第三处 sandbox 修复：Dockerfile COPY 路径须相对构建上下文（`COPY scenic_site/sitecustomize.py ...`，原计划误写 `sandbox/scenic_site/...`）。
- 新镜像 ID：**e18ef7b3d7aa**。
- 断网（--network none）三项验证全过：
  ① `np.object shim: True`（sitecustomize 经 site 自动导入，dask worker 子进程覆盖证明）；
  ② `offline import ok 0.12.1`（pyscenic/ctxcore/arboreto/common/scenic）；
  ③ 带挂载真跑 scenic.py（-v 双 DB :ro）：末行 JSON `ok: true`，grn 14.7s / ctx 21.0s / aucell 1.0s，2 regulons 与宿主一致；**dask LocalCluster 经 loopback 在 --network none 下正常工作**；CST7 schema 偶发未再现。

**Task 7 全量回归**
- **1045 passed**（1041+4，51.80s），0 failed，落 `.pytest_last.log`。
- 中途事故：settings `bio_scenic_db_root` 字段声明被子代理并行写竞争丢失，致 9 测试失败；补回声明后全绿（教训已录测试总结：并行写同一文件合并后须跑目标测试验证关键字段存在性）。

**Task 8 文档同步**
- ROADMAP 变更记录 +Phase 36 行；平台功能说明书 6.1 节 20→21 工具 + sc_scenic 表格行；使用说明书场景 D 产物表 +1 行 + 话术「帮我找各细胞类型的核心转录因子」；测试总结+2026-09-06T00-58-47.md 追加 Phase 36 实施节。
- 真机验收延后统一测试轮；累计待真机验收 **16 个工具（Phase 31-36）**。

**commit hash：7935609**

