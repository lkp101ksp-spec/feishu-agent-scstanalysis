"""sc_load：读入 h5ad/10x-mtx/.rds 数据 → 统一 raw.h5ad + 概要统计（Phase 20）。

stdin: {"path": "/data/xxx.h5ad", "dataset_id": "..."}（dataset_id 由
BioRunner 预计算注入；path 已是容器内 /data 视角路径）

2026-09-29 增 .rds 支持（PDAC 事故修复②）：Rscript rds2mtx.R 桥接成
mtx+obs.csv 再组装 AnnData；R 桥路径允许 RDS2MTX_BRIDGE 环境变量覆盖
（宿主侧单测用，容器内默认 /opt/r_tools/rds2mtx.R）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from common import DATA_ROOT, WS_ROOT, emit, fail, run

_RDS_BRIDGE = os.environ.get("RDS2MTX_BRIDGE", "/opt/r_tools/rds2mtx.R")


def _read_rds(p: Path) -> Any:
    """Rscript rds2mtx.R 桥接：rds → matrix.mtx/barcodes/features/obs.csv → AnnData。

    只支持 Seurat / SingleCellExperiment 对象；R 侧失败（类别不支持、
    无 counts 层等）统一报 SC_RDS_CONVERT_FAILED 并带回 R 报错尾部。
    """
    import anndata as ad
    import pandas as pd
    import scipy.io as sio

    with tempfile.TemporaryDirectory(prefix="rds2mtx_") as tmp:
        proc = subprocess.run(
            ["Rscript", _RDS_BRIDGE, str(p), tmp],
            capture_output=True, text=True, timeout=540,
        )
        if proc.returncode != 0:
            fail("SC_RDS_CONVERT_FAILED",
                 f"rds convert failed: {proc.stderr[-800:]}")
            raise SystemExit(1)
        tmp_path = Path(tmp)
        # writeMM 保持 R 侧方向（genes × cells）→ 转置为 AnnData cells × genes
        x = sio.mmread(tmp_path / "matrix.mtx").tocsr().T
        obs = pd.read_csv(tmp_path / "obs.csv", index_col=0)
        var = pd.DataFrame(index=pd.Index(
            (tmp_path / "features.tsv").read_text().splitlines(), name=None))
        adata = ad.AnnData(X=x, obs=obs, var=var)
    return adata


def _detect_and_read(path: str) -> Any:
    """h5ad 直读；目录视为 10x mtx（matrix.mtx + barcodes/features）；.rds 走 R 桥。"""
    import anndata as ad

    p = DATA_ROOT / path.lstrip("/")
    if not p.exists():
        fail("SC_FILE_NOT_FOUND", f"data file not found: /data/{path}")
        raise SystemExit(1)
    if p.suffix == ".h5ad":
        return ad.read_h5ad(p)
    if p.suffix == ".rds":
        return _read_rds(p)
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
         f"unsupported format (need .h5ad/.rds or 10x mtx dir): {path}")
    raise SystemExit(1)


def _velocity_diag(adata: Any) -> dict[str, Any]:
    """RNA velocity 前置校验：spliced/unspliced 层检测与覆盖度（建议⑤）。

    scVelo/velocyto 需剪接定量层；10x cellranger 总计数矩阵天然不含。
    返回字段并入 emit：
    - velocity_ready + velocity_layers（每层非零基因覆盖）
    - 缺层/全零时附 velocity_note（替代方案提示）
    """
    layers = getattr(adata, "layers", {})
    out: dict[str, Any] = {"velocity_ready": False}
    have = {k: k in layers for k in ("spliced", "unspliced")}
    if not all(have.values()):
        missing = [k for k, v in have.items() if not v]
        out["velocity_layers"] = have
        out["velocity_note"] = (
            f"missing layers: {','.join(missing)}；RNA velocity 需 "
            "velocyto/kallisto|bustools 定量的 spliced/unspliced 层"
            "（10x cellranger 总计数矩阵不含剪接信息）；可用 "
            "sc_pseudotime trajectory_full（Palantir 方向场）替代")
        return out
    import numpy as np

    stats: dict[str, dict[str, int]] = {}
    for k in ("spliced", "unspliced"):
        col_nnz = np.asarray((layers[k] > 0).sum(axis=0)).ravel()
        stats[k] = {"nonzero_genes": int((col_nnz > 0).sum()),
                    "total_genes": int(adata.n_vars)}
    out["velocity_layers"] = stats
    if stats["unspliced"]["nonzero_genes"] >= 10:
        out["velocity_ready"] = True
    else:
        out["velocity_note"] = (
            "unspliced 层几乎全零——定量失败或数据不含内含子 reads")
    return out


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
        **_velocity_diag(adata),
    })


if __name__ == "__main__":
    run(main)
