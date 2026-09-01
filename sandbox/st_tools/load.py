"""st_load：读入 visium/h5ad/mtx+坐标 → 统一 raw.h5ad + 概要统计（Phase 21）。

stdin: {"path": "/data/xxx", "dataset_id": "...", "format": "auto"}
path 指向：spaceranger 输出目录 / .h5ad 文件 / 含 matrix.mtx(+barcodes/
features) + coords.csv 的目录（coords.csv 需含 spot,x,y 三列）。
"""
from __future__ import annotations

from common import DATA_ROOT, WS_ROOT, emit, fail, run


def _read_visium(p):
    """spaceranger 目录：sq.read.visium（需 filtered_feature_bc_matrix.h5 + spatial/）。"""
    h5 = p / "filtered_feature_bc_matrix.h5"
    if not h5.exists():
        fail("ST_FORMAT_INVALID",
             f"not a spaceranger dir (filtered_feature_bc_matrix.h5 missing): {p}")
        raise SystemExit(1)
    if not (p / "spatial").is_dir():
        fail("ST_FORMAT_INVALID",
             f"spaceranger dir missing spatial/ subdir: {p}")
        raise SystemExit(1)
    import squidpy as sq

    return sq.read.visium(p, count_file=h5.name, library_id="st")


def _read_h5ad(p):
    """h5ad：直读 + 校验空间坐标。"""
    import anndata as ad

    adata = ad.read_h5ad(p)
    sp = adata.obsm.get("spatial")
    if sp is None:
        fail("ST_FORMAT_INVALID",
             f"h5ad has no obsm['spatial']: {p}")
        raise SystemExit(1)
    return adata


def _read_mtx_coords(p):
    """mtx + coords.csv：scanpy 读矩阵 + 坐标注入 obsm["spatial"]。"""
    import pandas as pd
    import scanpy as sc

    coords_csv = p / "coords.csv"
    if not coords_csv.exists():
        fail("ST_FORMAT_INVALID",
             f"mtx dir missing coords.csv (need spot,x,y columns): {p}")
        raise SystemExit(1)
    df = pd.read_csv(coords_csv)
    missing = [c for c in ("spot", "x", "y") if c not in df.columns]
    if missing:
        fail("ST_FORMAT_INVALID",
             f"coords.csv missing columns {missing}: {coords_csv}")
        raise SystemExit(1)

    # scanpy 只认 .gz：未压缩先临时 gzip 到 /tmp 再读（沿用 sc_load 方案）
    import gzip
    import shutil
    import tempfile
    from pathlib import Path

    mtx_dir = p
    if (p / "matrix.mtx").exists() and not (p / "matrix.mtx.gz").exists():
        tmp = tempfile.TemporaryDirectory()  # noqa: SIM115（读毕即弃）
        mtx_dir = Path(tempfile.gettempdir()) / tmp.name
        for name in ("matrix.mtx", "barcodes.tsv", "features.tsv", "genes.tsv"):
            src = p / name
            if src.exists():
                with open(src, "rb") as fin, \
                        gzip.open(mtx_dir / f"{name}.gz", "wb") as fout:
                    shutil.copyfileobj(fin, fout)
    adata = sc.read_10x_mtx(mtx_dir, var_names="gene_symbols", make_unique=True)

    if len(df) != adata.n_obs:
        fail("ST_FORMAT_INVALID",
             f"coords.csv rows ({len(df)}) != cells in matrix ({adata.n_obs})")
        raise SystemExit(1)
    adata = adata[df["spot"].astype(str).values.astype(adata.obs_names.dtype), :]
    adata.obsm["spatial"] = df[["x", "y"]].to_numpy(dtype="float64")
    return adata


def _detect_and_read(path: str):
    """auto 探测：h5ad 文件 / spaceranger 目录 / mtx+coords 目录。"""
    p = DATA_ROOT / path.lstrip("/")
    if not p.exists():
        fail("SC_FILE_NOT_FOUND", f"data not found: /data/{path}")
        raise SystemExit(1)
    if p.suffix == ".h5ad":
        return _read_h5ad(p)
    if p.is_dir():
        if (p / "filtered_feature_bc_matrix.h5").exists():
            return _read_visium(p)
        return _read_mtx_coords(p)
    fail("ST_FORMAT_UNSUPPORTED",
         f"unsupported format (need visium dir / .h5ad / mtx+coords dir): {path}")
    raise SystemExit(1)


def main() -> None:
    from common import read_args

    args = read_args()
    adata = _detect_and_read(args["path"])

    import numpy as np

    var_names = adata.var_names.astype(str)
    is_mt = var_names.str.startswith("MT-") | var_names.str.startswith("mt-")
    mt_summary = None
    if is_mt.any():
        import scanpy as sc

        adata.var["mt"] = is_mt
        sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                                   log1p=False, inplace=True)
        mt = adata.obs["pct_counts_mt"]
        mt_summary = {
            "mean": round(float(mt.mean()), 2),
            "median": round(float(mt.median()), 2),
            "p95": round(float(np.percentile(mt, 95)), 2),
        }

    import scanpy as sc

    sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False,
                               inplace=True)
    ds_dir = WS_ROOT / args["dataset_id"]
    ds_dir.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(ds_dir / "raw.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_spots": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "mt_pct": mt_summary,
        "workspace": str(ds_dir),
    })


if __name__ == "__main__":
    run(main)
