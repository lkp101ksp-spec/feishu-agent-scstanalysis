"""数据画像提取（Phase 42）：/research 规划前给 LLM 注入数据集统计。

动因（真机 2026-09-07）：planner 不知数据规模，sc_qc 套用默认
min_genes=600 而数据集 genes/cell 中位数仅 66 → SC_QC_OVERFILTERED
全过滤。本模块在规划前用 h5py 直接读 workspace 下的 h5ad（不启容器、
不依赖 anndata），提取 n_cells/n_genes/genes_per_cell/mt_pct 概要，
拼成画像文本注入 planner session_context，让模型按数据实况选阈值。

约定：
- dataset_ref = 12 位 hex（compute_dataset_id 的 sha1[:12]）；
- 读取链 raw.h5ad → filtered.h5ad → processed.h5ad（QC 面向 raw）；
- X 支持 dense array 与 CSR/CSC group 两种 anndata 编码，分块读行，
  大行数时抽样（默认最多 5000 行）控制耗时；
- 任何异常/缺文件 → 返回 None（画像是可选项，绝不影响规划主链路）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"\b[0-9a-f]{12}\b")
_SAMPLE_ROWS_CAP = 5000
_CHUNK_ROWS = 2000


def extract_dataset_refs(text: str) -> list[str]:
    """从任务文本提取 12 位 hex dataset_ref（去重保序）。"""
    seen: list[str] = []
    for m in _REF_RE.finditer(text):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def _pick_h5ad(ds_dir: Path) -> Path | None:
    """读取链：raw（QC 输入）→ filtered → processed。"""
    for name in ("raw.h5ad", "filtered.h5ad", "processed.h5ad"):
        p = ds_dir / name
        if p.is_file():
            return p
    return None


def _gene_names(var) -> list[str] | None:
    """var 组取基因名：优先 _index 数据集，其次 attrs['_index'] 指向列。"""
    import h5py  # 局部导入：模块加载不强依赖 h5py（无画像需求时零开销）

    if "_index" in var:
        raw = var["_index"][:]
    else:
        col = var.attrs.get("_index")
        if col and col in var:
            raw = var[col][:]
        else:
            return None
    return [g.decode() if isinstance(g, bytes) else str(g) for g in raw]


def _mt_mask(var, names: list[str] | None) -> np.ndarray | None:
    """线粒体基因掩码：优先 var['mt'] 布尔列，否则按 MT-/mt- 前缀派生。"""
    if "mt" in var:
        try:
            return np.asarray(var["mt"][:]).astype(bool)
        except Exception:  # noqa: BLE001
            pass
    if names:
        return np.array([g.startswith(("MT-", "mt-")) for g in names])
    return None


def _row_stats_dense(x, n_obs: int, mt_mask) -> tuple[np.ndarray, np.ndarray | None]:
    """dense X：分块算每细胞检测基因数与 mt 计数比例分子/分母。"""
    rows = np.arange(n_obs)
    if n_obs > _SAMPLE_ROWS_CAP:
        rng = np.random.default_rng(42)
        rows = np.sort(rng.choice(n_obs, _SAMPLE_ROWS_CAP, replace=False))
    genes = np.empty(len(rows), dtype=np.int64)
    mt_pct = np.empty(len(rows), dtype=np.float64) if mt_mask is not None else None
    for off in range(0, len(rows), _CHUNK_ROWS):
        idx = rows[off:off + _CHUNK_ROWS]
        block = np.asarray(x[idx, :])
        genes[off:off + len(idx)] = (block > 0).sum(axis=1)
        if mt_mask is not None:
            total = block.sum(axis=1)
            mt = block[:, mt_mask].sum(axis=1) if mt_mask.any() else 0.0
            mt_pct[off:off + len(idx)] = np.where(
                total > 0, mt / np.maximum(total, 1e-12) * 100.0, 0.0)
    return genes, mt_pct


def _row_stats_sparse(x, n_obs: int, mt_mask) -> tuple[np.ndarray, np.ndarray | None]:
    """CSR X（anndata csr_matrix 编码）：indptr 差分即检测基因数。"""
    indptr = x["indptr"][:]
    genes_all = np.diff(indptr).astype(np.int64)
    rows = np.arange(n_obs)
    if n_obs > _SAMPLE_ROWS_CAP:
        rng = np.random.default_rng(42)
        rows = np.sort(rng.choice(n_obs, _SAMPLE_ROWS_CAP, replace=False))
    genes = genes_all[rows]
    mt_pct = None
    if mt_mask is not None:
        data, indices = x["data"], x["indices"]
        mt_pct = np.empty(len(rows), dtype=np.float64)
        for k, r in enumerate(rows):
            s, e = indptr[r], indptr[r + 1]
            vals = data[s:e]
            cols = indices[s:e]
            total = float(vals.sum()) if e > s else 0.0
            mt = float(vals[mt_mask[cols]].sum()) if e > s else 0.0
            mt_pct[k] = mt / total * 100.0 if total > 0 else 0.0
    return genes, mt_pct


def profile_dataset(workspace_root: str, ref: str) -> dict | None:
    """读 workspace/<ref>/ 下的 h5ad，返回画像 dict；失败/缺文件 → None。

    返回字段：ref/n_cells/n_genes/genes_per_cell{median,p90,max}/
    mt_pct{median,p95}（无线粒体基因时缺省）/source（用了哪个 h5ad）。
    """
    if not workspace_root:
        return None
    ds_dir = Path(workspace_root) / ref
    if not ds_dir.is_dir():
        return None
    h5 = _pick_h5ad(ds_dir)
    if h5 is None:
        return None
    try:
        import h5py

        with h5py.File(h5, "r") as f:
            if "X" not in f:
                return None
            x = f["X"]
            names = _gene_names(f["var"]) if "var" in f else None
            mt_mask = _mt_mask(f["var"], names) if "var" in f else None
            if isinstance(x, h5py.Dataset):  # dense array 编码
                n_obs, n_vars = x.shape
                genes, mt_pct = _row_stats_dense(x, n_obs, mt_mask)
            elif isinstance(x, h5py.Group) and "indptr" in x:  # CSR/CSC
                shape = x.attrs.get("shape")
                if shape is None:
                    return None
                if x.attrs.get("encoding-type") == "csc_matrix":
                    return None  # 罕见，暂不支持（画像缺失可接受）
                n_obs, n_vars = int(shape[0]), int(shape[1])
                genes, mt_pct = _row_stats_sparse(x, n_obs, mt_mask)
            else:
                return None
        out = {
            "ref": ref,
            "source": h5.name,
            "n_cells": int(n_obs),
            "n_genes": int(n_vars),
            "genes_per_cell": {
                "median": float(np.median(genes)),
                "p90": float(np.percentile(genes, 90)),
                "max": int(genes.max()) if len(genes) else 0,
            },
        }
        if mt_pct is not None:
            out["mt_pct"] = {
                "median": float(np.median(mt_pct)),
                "p95": float(np.percentile(mt_pct, 95)),
            }
        return out
    except Exception:  # noqa: BLE001 —— 画像是可选项，失败静默
        logger.warning("dataset profile failed for %s", ref, exc_info=True)
        return None


def build_profile_context(text: str, workspace_root: str) -> str:
    """任务文本中的 dataset_ref → 画像文本段（planner 注入用）。

    无可识别 ref / 画像失败 → 空串（调用方不追加）。
    """
    if not workspace_root:
        return ""
    profiles = []
    for ref in extract_dataset_refs(text)[:3]:  # 最多 3 个，防 prompt 膨胀
        p = profile_dataset(workspace_root, ref)
        if p:
            profiles.append(p)
    if not profiles:
        return ""
    lines = ["数据画像（真实数据统计，sc_* 参数必须按此选择）："]
    for p in profiles:
        g = p["genes_per_cell"]
        seg = (
            f"- {p['ref']}（{p['source']}）：{p['n_cells']} 细胞 × "
            f"{p['n_genes']} 基因；每细胞检测基因数 median={g['median']:.0f} "
            f"p90={g['p90']:.0f} max={g['max']}"
        )
        if "mt_pct" in p:
            m = p["mt_pct"]
            seg += f"；线粒体比例 median={m['median']:.1f}% p95={m['p95']:.1f}%"
        lines.append(seg)
    lines.append(
        "sc_qc 的 min_genes 须明显低于 median（建议 ≤ median/2），"
        "max_mt_pct 参考 p95 上调；严禁套用默认值不看数据规模。")
    return "\n".join(lines)
