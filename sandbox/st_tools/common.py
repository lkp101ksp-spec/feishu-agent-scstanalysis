"""st 工具公共库：stdin JSON 参数 → stdout JSON 结果（Phase 21 st 镜像内）。

约定与 sc_tools/common.py 一致（spec phase21 §2）：
- 参数经 stdin 传入 JSON；结果 stdout 输出 JSON（{"ok": true, ...} /
  {"ok": false, "error_code": ..., "error_message": ...}）
- 图一律落 /ws；数据路径限定 /data（只读挂载）与 /ws
- matplotlib 无头模式 + 缓存目录指向可写 tmp
ST 专用：spatial coords 校验（ensure_spatial）。
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

DATA_ROOT = Path("/data")
WS_ROOT = Path("/ws")


def read_args() -> dict:
    """读 stdin JSON 参数；空 stdin 返回空 dict。"""
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def emit(obj: dict) -> None:
    """结果 JSON 写 stdout（唯一 stdout 输出，图/日志走 stderr）。"""
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def fail(error_code: str, error_message: str) -> None:
    """统一失败输出。"""
    emit({"ok": False, "error_code": error_code, "error_message": error_message})


def run(main) -> None:
    """脚本入口包装：异常吃掉转统一 JSON 错误（含 traceback 首 3 帧）。"""
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 —— 参数化脚本兜底统一报错
        tb = traceback.format_exc(limit=3)
        print(tb, file=sys.stderr)
        fail("SCRIPT_ERROR", f"{type(e).__name__}: {e}")


def ensure_spatial(adata) -> None:
    """校验空间坐标存在于 obsm["spatial"] 且形状合法（ST 下游脚本前置）。

    抛 ValueError（脚本层转 ST_FORMAT_INVALID 语义）当坐标缺失或
    形状不是 (n_spots, >=2)。
    """
    import numpy as np

    sp = adata.obsm.get("spatial")
    if sp is None:
        raise ValueError(
            "spatial coordinates missing: obsm['spatial'] not found; "
            "load with st_load (visium dir / h5ad with spatial / mtx+coords csv)")
    sp = np.asarray(sp)
    if sp.ndim != 2 or sp.shape[1] < 2 or sp.shape[0] != adata.n_obs:
        raise ValueError(
            f"invalid spatial coords shape {sp.shape}: expect (n_spots, >=2) "
            f"matching n_obs={adata.n_obs}")


def load_adata(input_ref: dict):
    """按回退链读 AnnData：filtered.h5ad → raw.h5ad（st 与 sc 同模式）。"""
    import anndata as ad

    ds_dir = WS_ROOT / input_ref["dataset_id"]
    for name in ("filtered", "raw"):
        p = ds_dir / f"{name}.h5ad"
        if p.exists():
            return ad.read_h5ad(p)
    raise FileNotFoundError(
        f"no h5ad under workspace for dataset {input_ref['dataset_id']}; "
        "run st_load first")
