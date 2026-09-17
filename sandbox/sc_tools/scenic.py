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

import contextlib
import io
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from dask.distributed import Client, LocalCluster

# numpy>=1.24 移除的别名 shim（pyscenic 0.12.1 transform/diptest/rss 仍引用）；
# 须在 import pyscenic 任何子模块之前执行。容器内另有 site-packages/
# sitecustomize.py 覆盖 dask worker 子进程。
for _alias, _typ in {"object": object, "float": float, "int": int,
                     "str": str}.items():
    if not hasattr(np, _alias):
        setattr(np, _alias, _typ)

from common import WS_ROOT, emit, load_adata, read_args, run, species_style_guard  # noqa: E402

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

    # distributed 带 py.typed 但 LocalCluster/Client 构造、compute、close
    # 无注解——no-untyped-call 经 callable 别名收口；真类型走 TYPE_CHECKING
    # 导入，cast 首参必须字符串形式（运行时不求值）
    _mk_cluster = cast("Callable[..., LocalCluster]", LocalCluster)
    _mk_client = cast("Callable[..., Client]", Client)
    cluster = _mk_cluster(n_workers=n_workers, threads_per_worker=1,
                          processes=True, dashboard_address=None,
                          silence_logs=30)
    client = _mk_client(cluster)
    try:
        matrix, gene_names, tf_names = _prepare_input(expr, None, tfs)
        links_graph, _meta_graph = create_graph(
            matrix, gene_names, tf_names, "GBM", SGBM_KWARGS, client,
            include_meta=True, seed=seed)
        _compute = cast("Callable[..., pd.DataFrame]", client.compute)
        adj = _compute(links_graph, sync=True).sort_values(
            by="importance", ascending=False)
    finally:
        _close = cast("Callable[[], None]", client.close)
        _close()
        cluster.close()
    return adj


def _prune(dbs: list[Any], modules: list[Any], motif_tbl: Path,
           n_workers: int) -> pd.DataFrame:
    """cisTarget motif 剪枝：prune2df 把 generator 传给新版 dask
    from_delayed（无 len）→ monkeypatch 先物化 list。
    另：默认 dask_multiprocessing 调度 spawn 的子进程不继承进程内
    numpy 别名 shim（worker import pyscenic.transform 时 np.object 即炸），
    改为传入自建的线程式 LocalCluster Client（processes=False，
    与主进程同址，shim 生效；sitecustomize 仍是容器兜底）。

    ctxcore 0.2.0 增量 prefetch 缓存 bug：difference 未排除已加载列，
    append_column 产生重名列后 select 报 'Field X exists 2 times in
    schema'（或缓存状态错乱报 'does not exist'）；而逐模块全量重读
    feather 太慢（实测 >3600s）。改为一次性预取「全模块基因并集」
    （一次列存读），后续模块调用全部命中缓存、不再触碰增量分支。"""
    import pyscenic.prune as prune_mod
    from ctxcore.ctdb import CisTargetDatabase
    from dask.distributed import Client, LocalCluster
    from pyscenic.prune import prune2df

    union_genes = {g for m in modules for g in m.genes}
    import threading
    _prefetch_lock = threading.Lock()  # 防线程并发重复全读大表（OOM 风险）

    def _log(msg: str) -> None:  # stderr 进度（stdout 是 JSON 契约，不能碰）
        import sys
        print(f"[scenic] {msg}", file=sys.stderr, flush=True)

    def _union_prefetch(self: Any, region_or_gene_ids: Any,
                        sort: bool = False) -> None:
        if getattr(self, "_scenic_union_done", False):
            return  # 并集已覆盖所有模块基因，后续调用无需再取数
        with _prefetch_lock:
            if getattr(self, "_scenic_union_done", False):
                return
            import pyarrow.feather as pf
            avail = set(self.all_region_or_gene_ids.ids)
            want = sorted(union_genes & avail)
            motif_col = self.all_motif_or_track_ids.type.value
            # 顺序整表读（挂载盘上按列子集随机读极慢，实测卡 I/O 超 40min）；
            # 全表 ~1GB 顺序读仅数秒，再在内存中 select 并集列
            _log(f"union prefetch start: {len(want)} genes from "
                 f"{Path(self.ct_db_filename).name}")
            full = pf.read_table(self.ct_db_filename)
            self.df_cached = full.select(want + [motif_col])
            self._scenic_union_done = True
            _log(f"union prefetch done: cached "
                 f"{self.df_cached.num_columns} cols")

    CisTargetDatabase._prefetch_as_pyarrow_table = _union_prefetch

    _orig_fd = prune_mod.from_delayed

    def _fd_compat(dfs: Any, *args: Any, **kwargs: Any) -> Any:
        """dask-expr 的 from_delayed 不接受 generator，先物化。"""
        if not isinstance(dfs, (list, tuple)):
            dfs = list(dfs)
        return _orig_fd(dfs, *args, **kwargs)

    prune_mod.from_delayed = _fd_compat
    _log(f"ctx start: {len(modules)} modules x {len(dbs)} db(s), "
         f"workers={n_workers}")
    # memory_limit=0 关闭 worker 内存管理：容器 --memory 16g 时 distributed
    # 按 cgroup 限额算 spill 阈值（0.6×16=9.6GB），并集缓存表把 RSS 顶到
    # 阈值后 worker 陷入 spill/pause 死循环（CPU 3% 假死，实测两物种复现）
    _mk_cluster = cast("Callable[..., LocalCluster]", LocalCluster)
    _mk_client = cast("Callable[..., Client]", Client)
    cluster = _mk_cluster(n_workers=n_workers, threads_per_worker=1,
                          processes=False, dashboard_address=None,
                          silence_logs=30, memory_limit=0)
    client = _mk_client(cluster)
    try:
        df = prune2df(dbs, modules, str(motif_tbl),
                      client_or_address=client, num_workers=n_workers)
        _log(f"ctx done: {len(df)} enriched rows")
        return df
    finally:
        _close = cast("Callable[[], None]", client.close)
        _close()
        cluster.close()


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
    species_style_guard(species, counts_ad.var_names, "SC_SPECIES_MISMATCH")
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
    var_names = sub.var_names.astype(str)
    dup = pd.Index(var_names).duplicated()
    if dup.any():  # 同名基因（10x 常见）只留首个，否则 pyarrow schema 撞重名字段
        keep = keep & ~dup
    genes = var_names[keep]
    if len(genes) < 50:
        raise ValueError(f"too few genes after filtering: {len(genes)} (<50)")
    Xd = sub[:, genes].X
    Xd = Xd.toarray() if sp.issparse(Xd) else np.asarray(Xd)
    expr = pd.DataFrame(Xd, index=cells, columns=genes)
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
    # df2regulons 会往 stdout 打印（"Create regulons..."），污染 JSON 契约，屏蔽
    with contextlib.redirect_stdout(io.StringIO()):
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

    result: dict[str, Any] = {
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
    # rss 为 cell_type × regulon（行=簇、列=regulon，pyscenic 0.12.1）
    auc_cluster = auc.groupby(labels).mean()
    top_regs = []
    for c in rss.index:
        top_regs.extend(rss.loc[c].nlargest(5).index.tolist())
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
            str(c): [str(r) for r in rss.loc[c].nlargest(3).index]
            for c in rss.index},
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
