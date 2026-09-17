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
from typing import Any, Callable, cast

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


def read_args() -> dict[str, Any]:
    """读 stdin JSON 参数；空 stdin 返回空 dict。"""
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return cast(dict[str, Any], json.loads(raw))


def emit(obj: dict[str, Any]) -> None:
    """结果 JSON 写 stdout（唯一 stdout 输出，图/日志走 stderr）。"""
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def fail(error_code: str, error_message: str) -> None:
    """统一失败输出。"""
    emit({"ok": False, "error_code": error_code, "error_message": error_message})


def run(main: Callable[[], None]) -> None:
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


def load_adata(input_ref: dict[str, Any]) -> Any:
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


def upper_gene_map(var_names: Any, target_genes: Any) -> list[str]:
    """按 str.upper() 对齐匹配 target_genes 到 var_names，返回原始 var 名（保序去重）。

    小鼠符号（Mki67）与人源资源（MKI67）的大小写桥接；跳过一对多/多对一的
    大小写歧义冲突（如同一 upper 对应多个 var 名时全部丢弃，宁可少配不错配）。
    """
    upper_to_var: dict[str, str] = {}
    ambiguous: set[str] = set()
    for name in var_names:
        key = str(name).upper()
        if key in upper_to_var:
            ambiguous.add(key)
        else:
            upper_to_var[key] = str(name)
    for key in ambiguous:
        upper_to_var.pop(key, None)
    seen: set[str] = set()
    matched: list[str] = []
    for gene in target_genes:
        var = upper_to_var.get(str(gene).upper())
        if var is not None and var not in seen:
            seen.add(var)
            matched.append(var)
    return matched


def detect_symbol_style(var_names: Any) -> str:
    """基因符号风格检测：title（Xkr4 鼠式）/ upper（XKR4 人式）/
    ensembl（ENSG*/ENSMUS*）/ mixed（不确定）。"""
    names = [str(g) for g in var_names]
    n = max(len(names), 1)
    title = sum(1 for g in names
                if g[:1].isalpha() and g[:1].isupper()
                and any(c.islower() for c in g))
    upper = sum(1 for g in names
                if any(c.isalpha() for c in g) and g == g.upper())
    ensembl = sum(1 for g in names if g.startswith(("ENSG", "ENSMUS")))
    if ensembl / n > 0.5:
        return "ensembl"
    if title / n > 0.5:
        return "title"
    if upper / n > 0.5:
        return "upper"
    return "mixed"


def species_style_guard(species: str, var_names: Any,
                        error_code: str) -> None:
    """species 与基因符号风格矛盾时快败（数据集口径记忆库第二层兜底）。

    Phase 61 教训：payload species=human 而数据实为鼠源（Xkr4/Gm1992），
    资源库交集在计算深处才报难解错误（R 桥 7 分钟白烧）。此卫兵在
    load 后毫秒级拦截并给出可执行改法；mixed 风格不拦（不确定不武断）。
    """
    style = detect_symbol_style(var_names)
    expect = {"human": "upper", "mouse": "title"}.get(species)
    if style == "ensembl":
        fail(error_code, "基因符号为 Ensembl ID（ENSG/ENSMUS），物种资源库"
             "无法匹配；需先转换为基因 symbol 再运行本工具")
        raise SystemExit(1)
    if expect and style in ("title", "upper") and style != expect:
        hint = ("mouse（符号 Title-case，如 Xkr4/Rp1）" if style == "title"
                else "human（符号全大写，如 TGFB1/XKR4）")
        fail(error_code, f"species={species} 与数据基因符号风格矛盾："
             f"符号为 {style} 风格 → 数据集应为 {hint}；"
             f"请改 species 重跑（或确认数据未做过符号映射）")
        raise SystemExit(1)
