"""sc_load：读入 h5ad/10x-mtx 数据 → 统一 raw.h5ad + 概要统计（Phase 20）。

stdin: {"path": "/data/xxx.h5ad", "dataset_id": "..."}（dataset_id 由
BioRunner 预计算注入；path 已是容器内 /data 视角路径）
"""
from __future__ import annotations

from pathlib import Path

from common import DATA_ROOT, WS_ROOT, emit, fail, run


def _detect_and_read(path: str):
    """h5ad 直读；目录视为 10x mtx（matrix.mtx + barcodes/features）。"""
    import anndata as ad

    p = DATA_ROOT / path.lstrip("/")
    if not p.exists():
        fail("SC_FILE_NOT_FOUND", f"data file not found: /data/{path}")
        raise SystemExit(1)
    if p.suffix == ".h5ad":
        return ad.read_h5ad(p)
    if p.is_dir():
        # 新版 anndata 无 read_10x_mtx，统一走 scanpy；
        # scanpy 只认 .gz 文件——未压缩 10x 目录先临时 gzip 到 /tmp 再读
        import gzip
        import shutil
        import tempfile

        import scanpy as sc

        mtx_dir = p
        if (p / "matrix.mtx").exists() and not (p / "matrix.mtx.gz").exists():
            tmp = tempfile.TemporaryDirectory()  # 读毕即弃
            mtx_dir = Path(tempfile.gettempdir()) / tmp.name
            for name in ("matrix.mtx", "barcodes.tsv", "features.tsv",
                         "genes.tsv"):
                src = p / name
                if src.exists():
                    with open(src, "rb") as fin, \
                            gzip.open(mtx_dir / f"{name}.gz", "wb") as fout:
                        shutil.copyfileobj(fin, fout)
        return sc.read_10x_mtx(mtx_dir, var_names="gene_symbols",
                               make_unique=True)
    fail("SC_FORMAT_UNSUPPORTED",
         f"unsupported format (need .h5ad or 10x mtx dir): {path}")
    raise SystemExit(1)


def main() -> None:
    from common import read_args

    args = read_args()
    adata = _detect_and_read(args["path"])

    # 线粒体比例（人/鼠通用前缀 MT-/mt-）
    import numpy as np

    var_names = adata.var_names.astype(str)
    is_mt = var_names.str.startswith("MT-") | var_names.str.startswith("mt-")
    mt_key = "mt" if is_mt.any() else None
    if mt_key:
        import scanpy as sc

        adata.var["mt"] = is_mt  # calculate_qc_metrics 依赖该 var 列

        sc.pp.calculate_qc_metrics(adata, qc_vars=[mt_key],
                                   percent_top=None, log1p=False,
                                   inplace=True)
        mt_pct_field = "pct_counts_mt"
        mt_values = adata.obs[mt_pct_field]
        mt_summary = {
            "mean": round(float(mt_values.mean()), 2),
            "median": round(float(mt_values.median()), 2),
            "p95": round(float(np.percentile(mt_values, 95)), 2),
        }
    else:
        mt_summary = None

    # 落统一格式 raw.h5ad（幂等：重复 load 同 dataset 直接覆盖）
    import scanpy as sc

    sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False,
                               inplace=True)
    ds_dir = WS_ROOT / args["dataset_id"]
    ds_dir.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(ds_dir / "raw.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "mt_pct": mt_summary,
        "workspace": str(ds_dir),
    })


if __name__ == "__main__":
    run(main)
