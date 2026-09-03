"""sc 工具公共库：stdin JSON 参数 → stdout JSON 结果（Phase 20 bio 镜像内）。

约定（spec 2026-09-01 phase20 §3.2）：
- 参数经 stdin 传入 JSON；结果 stdout 输出 JSON（{"ok": true, ...} /
  {"ok": false, "error_code": ..., "error_message": ...}）
- 图一律落 /ws（workspace 挂载点）；数据路径限定 /data（只读挂载）与 /ws
- matplotlib 无头模式 + 缓存目录指向可写 tmp
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

# 新版 anndata（GPU 镜像 ≥0.11）默认拒绝写 nullable string 列；
# 显式回退 anndata<0.11 旧格式，保证 CPU/GPU 镜像产物互相可读。
# 旧版 anndata 无此设置项，赋值无害。
try:
    import anndata as _ad

    _ad.settings.allow_write_nullable_strings = False
except Exception:  # noqa: BLE001 —— 旧版无该设置时跳过
    pass

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


def ws_path(name: str) -> Path:
    """workspace 内文件路径（防参数注入任意路径：限定 /ws 下单文件名）。"""
    p = (WS_ROOT / name).resolve()
    if WS_ROOT not in p.parents and p != WS_ROOT:
        raise ValueError(f"invalid workspace file name: {name!r}")
    return p


def load_adata(input_ref: dict):
    """按回退链读 AnnData：filtered.h5ad → raw.h5ad（spec §4）。

    input_ref: {"dataset_id": str, "file": "filtered"|"raw"|"processed"}
    """
    import anndata as ad

    ds_dir = WS_ROOT / input_ref["dataset_id"]
    if input_ref.get("file") == "processed":
        p = ds_dir / "processed.h5ad"
        if not p.exists():
            raise FileNotFoundError(
                "processed.h5ad not found; run sc_process first")
        return ad.read_h5ad(p)
    for name in ("filtered", "raw"):
        p = ds_dir / f"{name}.h5ad"
        if p.exists():
            return ad.read_h5ad(p)
    raise FileNotFoundError(
        f"no h5ad under workspace for dataset {input_ref['dataset_id']}; "
        "run sc_load first")
