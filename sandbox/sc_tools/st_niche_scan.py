"""st_niche_scan：全 niche NicheNet 批量扫描（Phase 68，L3 工具）。

薄编排层：枚举 groupby 中 ≥min_spots 的全部 receiver niche → 逐
niche 数据驱动 geneset（niche 内 vs 其余 spot 的 normalized-log 均值
差上调 top N，剔 MT-/RPL/RPS）→ subprocess 自调同目录 st_nichenet.py
（零改动黑盒复用：分环/sender 母体/KB 桥/slug 产物/绘图）→ 汇总
配体×niche aupr 宽表 + 热图 + summary JSON。

stdin 契约（除 dataset_id 外全可选）：
  {"dataset_id": "oscc", "groupby": "spatial_domain", "min_spots": 5,
   "n_geneset": 30, "max_rings": 1, "knn": 6, "species": "human",
   "top_n_ligands": 30, "min_expr": 0.1}

产物（落 /ws/{ds}/，每 niche 5 件 slug 产物由 st_nichenet 生成）：
  nichenet_allniche_matrix.csv    ligands × niches aupr 宽表
  nichenet_allniche_heatmap.png   同数据热图（aupr 原值）
  nichenet_allniche_summary.json  niches 元数据/top_ligands/failures

容错：单 niche 失败（子进程超时/非零退出/非法 JSON/工具侧 fail）
记入 failures 后 continue；剩余墙钟 <300s 时剩余 niche 记
TIME_BUDGET；仅全 niche 失败才顶层 fail。时间预算自管：外层
TOOL_TIMEOUT(3600s) 内留 180s 收尾余量，防 Docker 硬杀丢失 emit。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, read_args, run

SCRIPT = Path(__file__).with_name("st_nichenet.py")
TOOL_TIMEOUT = 3600  # 与 L3 ToolSpec.timeout_sec 一致（契约测试钉死）
TAIL_MARGIN = 180  # 收尾汇总/emit 余量
NICHE_TIMEOUT_CAP = 1800  # 单 niche 上限（与 st_nichenet ToolSpec 档一致）
BUDGET_FLOOR = 300  # 剩余低于此值 → 剩余 niche 记 TIME_BUDGET
TOP_PER_NICHE = 10  # 汇总矩阵每 niche 取 top N 配体（探针同口径）

BAD_PREFIX = ("MT-", "RPL", "RPS")


def _slug(niche: str) -> str:
    """与 st_nichenet 工具侧一致的 receiver slug 规则（产物后缀/键名）。"""
    return re.sub(r"[^0-9A-Za-z]+", "_", niche).strip("_").lower()[:40] or "niche"


def _niche_up_genes(x: Any, var_names: pd.Index, cl: np.ndarray, niche: str, top_n: int) -> list[str]:
    """数据驱动 geneset：niche 内 vs 其余 spot 的均值差上调 top N
    （raw 快照口径，剔 MT-/RPL/RPS；与探针 niche_up_genes 逐位同口径）。"""
    inm = cl == niche
    mu_in = np.asarray(x[inm].mean(0)).ravel()
    mu_out = np.asarray(x[~inm].mean(0)).ravel()
    order = np.argsort(-(mu_in - mu_out))
    genes = [str(g) for g in np.asarray(var_names)[order]]
    return [g for g in genes if not g.startswith(BAD_PREFIX)][:top_n]


def _load_and_validate(ds: str, groupby: str) -> tuple[Path, Any, np.ndarray, pd.Index]:
    """前置校验一次 + 读库：返回 (ds_dir, x, cl, var_names)。
    校验失败直接 fail（不让每个 niche 重复报同类错）。"""
    ds_dir = WS_ROOT / ds
    p = ds_dir / "processed.h5ad"
    if not p.exists():
        fail(
            "INVALID_INPUT",
            f"no processed.h5ad under dataset {ds!r} — run st_process "
            "first (need obsm.spatial + obs groups + normalized X)",
        )
        raise SystemExit(1)
    import anndata as ad

    aw = ad.read_h5ad(p)
    if "spatial" not in aw.obsm:
        fail(
            "ST_FORMAT_INVALID",
            "processed.h5ad lacks obsm['spatial'] — run st_process "
            "(spatial coordinates required for niche rings)",
        )
        raise SystemExit(1)
    x: Any = aw.X
    var_names = pd.Index(aw.var_names.astype(str))
    if x is not None and x.size and float(x.min()) < 0:  # scaled → 回退 .raw
        if aw.raw is not None:
            x = aw.raw.X
            var_names = pd.Index(aw.raw.var_names.astype(str))
        else:
            fail(
                "INVALID_INPUT",
                "processed.h5ad X appears scaled (negative values) "
                "with no .raw fallback — cannot compute geneset means",
            )
            raise SystemExit(1)
    if groupby not in aw.obs:
        fail(
            "ST_NICHESCAN_NO_GROUP",
            f"groupby column {groupby!r} not in obs; available: {list(aw.obs.columns)[:20]}",
        )
        raise SystemExit(1)
    cl = aw.obs[groupby].astype(str).to_numpy()
    return ds_dir, x, cl, var_names


def _run_one(niche: str, gs: list[str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    """单 niche subprocess 调 st_nichenet.py：返回其 stdout dict；
    超时抛 TimeoutExpired；非零退出/非法 JSON 抛 RuntimeError。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({**payload, "geneset": gs, "receiver_niche": niche}),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        out: dict[str, Any] = json.loads(proc.stdout)
    except (json.JSONDecodeError, IndexError) as e:
        raise RuntimeError(f"st_nichenet stdout not JSON (rc={proc.returncode}): {proc.stderr[-600:]}") from e
    if not out.get("ok"):
        raise RuntimeError(
            f"st_nichenet failed: {out.get('error_code')}: {out.get('error_message', '')[:300]}"
        )
    return out


def _summarize(
    ds_dir: Path,
    rows: dict[str, dict[str, float]],
    meta: dict[str, dict[str, Any]],
    failures: dict[str, dict[str, Any]],
    groupby: str,
) -> tuple[str, str, str]:
    """汇总落盘：aupr 宽表 + 热图 + summary JSON（探针同口径，
    aupr 原值不做 z-score；矩阵行按全局最大 aupr 降序）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: E402

    mat = pd.DataFrame(rows).fillna(np.nan)  # 行=配体 列=niche
    mat = mat.loc[mat.max(axis=1).sort_values(ascending=False).index]
    mat_p = ds_dir / "nichenet_allniche_matrix.csv"
    mat.to_csv(mat_p, encoding="utf-8-sig")

    nr, nc = mat.shape
    fig, ax = plt.subplots(figsize=(2.0 + 1.15 * nc, 0.34 * nr + 1.6))
    im = ax.imshow(mat.values, aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(nc), mat.columns, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(nr), mat.index, fontsize=7)
    if nr * nc <= 480:
        for r in range(nr):
            for c in range(nc):
                v = mat.values[r, c]
                if not np.isnan(v):
                    ax.text(c, r, f"{v:.3f}", ha="center", va="center", fontsize=6, color="black")
    fig.colorbar(im, ax=ax, fraction=0.03, label="aupr_corrected")
    ax.set_title(f"all-niche NicheNet top ligands (groupby={groupby})", fontsize=9)
    fig.tight_layout()
    hm_p = ds_dir / "nichenet_allniche_heatmap.png"
    fig.savefig(hm_p, dpi=150)
    plt.close(fig)

    sm_p = ds_dir / "nichenet_allniche_summary.json"
    sm_p.write_text(
        json.dumps(
            {
                "groupby": groupby,
                "n_niches_ok": len(meta),
                "n_niches_failed": len(failures),
                "niches": meta,
                "failures": failures,
                "ligands_shared_all_niches": sorted(
                    set.intersection(*[set(r) for r in rows.values()]) if rows else []
                ),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return str(mat_p), str(hm_p), str(sm_p)


def main() -> None:
    """全 niche 编排主链：枚举（前置）→ 逐 niche subprocess nichenet
    → 汇总落盘 → emit（克制的 top 与计数，完整表靠落盘文件）。"""
    t0 = time.monotonic()
    args = read_args()
    ds = str(args["dataset_id"])
    groupby = str(args.get("groupby", "spatial_domain"))
    min_spots = int(args.get("min_spots", 5))
    n_geneset = int(args.get("n_geneset", 30))
    max_rings = int(args.get("max_rings", 1))
    knn = int(args.get("knn", 6))
    species = str(args.get("species", ""))
    top_n = int(args.get("top_n_ligands", 30))
    min_expr = float(args.get("min_expr", 0.1))

    if min_spots < 1:
        fail("INVALID_INPUT", f"min_spots must be >= 1 (got {min_spots})")
        return
    if not 10 <= n_geneset <= 100:
        fail("INVALID_INPUT", f"n_geneset must be in [10, 100] (got {n_geneset})")
        return
    if not 1 <= max_rings <= 5:
        fail("INVALID_INPUT", f"max_rings must be in [1, 5] (got {max_rings})")
        return
    if not 4 <= knn <= 20:
        fail("INVALID_INPUT", f"knn must be in [4, 20] (got {knn})")
        return
    if species and species not in ("mouse", "human"):
        fail("INVALID_INPUT", f"species must be 'mouse' or 'human' (got {species!r}; empty = auto-detect)")
        return

    ds_dir, x, cl, var_names = _load_and_validate(ds, groupby)

    # 枚举 receiver niche：计数降序，≥min_spots（探针同口径）
    vc = pd.Series(cl).value_counts()
    niches = [str(v) for v, n in vc.items() if n >= min_spots]
    if not niches:
        fail(
            "INVALID_INPUT",
            f"no niche with >= {min_spots} spots in {groupby!r} "
            f"(counts: {dict(vc.head(10))}) — lower min_spots",
        )
        return

    base: dict[str, Any] = {
        "dataset_id": ds,
        "groupby": groupby,
        "max_rings": max_rings,
        "knn": knn,
        "species": species,
        "top_n_ligands": top_n,
        "min_expr": min_expr,
    }

    budget = TOOL_TIMEOUT - TAIL_MARGIN
    results: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict[str, float]] = {}
    meta: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, Any]] = {}
    for niche in niches:
        elapsed = time.monotonic() - t0
        remain = budget - elapsed
        if remain < BUDGET_FLOOR:
            failures[niche] = {
                "error_code": "TIME_BUDGET",
                "error_message": f"remaining budget {remain:.0f}s < {BUDGET_FLOOR}s, skipped",
            }
            continue
        gs = _niche_up_genes(x, var_names, cl, niche, n_geneset)
        try:
            out = _run_one(niche, gs, base, min(NICHE_TIMEOUT_CAP, int(remain) - 60))
        except subprocess.TimeoutExpired:
            failures[niche] = {
                "error_code": "NICHE_TIMEOUT",
                "error_message": f"st_nichenet exceeded {min(NICHE_TIMEOUT_CAP, int(remain) - 60)}s",
            }
            continue
        except RuntimeError as e:
            failures[niche] = {"error_code": "SCRIPT_ERROR", "error_message": str(e)[:400]}
            continue
        top_lig = {str(k): float(v) for k, v in (out.get("top_ligands") or {}).items()}
        rows[niche] = dict(sorted(top_lig.items(), key=lambda kv: -kv[1])[:TOP_PER_NICHE])
        results[niche] = {
            "n_sender": out.get("n_sender"),
            "n_receiver": out.get("n_receiver"),
            "n_geneset_used": out.get("n_geneset_used"),
            "n_ligands_tested": out.get("n_ligands_tested"),
            "ring_counts": out.get("ring_counts"),
            "top_ligands": rows[niche],
            "ligand_activities_csv": out.get("ligand_activities_csv"),
        }
        meta[niche] = {
            "n_spots": int((cl == niche).sum()),
            "geneset_n": len(gs),
            "n_sender": out.get("n_sender"),
            "ring_counts": out.get("ring_counts"),
            "top_ligands": list(rows[niche]),
        }

    if not rows:
        fail(
            "ST_NICHESCAN_ALL_FAILED",
            f"all {len(niches)} niche(s) failed: {json.dumps(failures, ensure_ascii=False)[:1200]}",
        )
        return

    mat_p, hm_p, sm_p = _summarize(ds_dir, rows, meta, failures, groupby)
    emit(
        {
            "ok": True,
            "n_spots": int(cl.size),
            "groupby": groupby,
            "n_niches_total": len(niches),
            "n_niches_ok": len(results),
            "n_niches_failed": len(failures),
            "niches": niches,
            "results": results,
            "failures": failures,
            "matrix_csv": mat_p,
            "heatmap_png": hm_p,
            "summary_json": sm_p,
        }
    )


run(main)
