"""数据画像提取（Phase 42）：/research 规划前给 LLM 注入数据集统计。

动因（真机 2026-09-07）：planner 不知数据规模，sc_qc 套用默认
min_genes=600 而数据集 genes/cell 中位数仅 66 → SC_QC_OVERFILTERED
全过滤。本模块在规划前用 h5py 直接读 workspace 下的 h5ad（不启容器、
不依赖 anndata），提取 n_cells/n_genes/genes_per_cell/mt_pct 概要，
拼成画像文本注入 planner session_context，让模型按数据实况选阈值。

数据集口径记忆库（执行面统一轮，Phase 61 教训转化）：profile 额外
检测 species 猜测（基因符号风格：Title-case=mouse / 全大写=human /
Ensembl ID）与 obs 可用列，并持久化到 <workspace_root>/_profiles/
<ref>.json——下划线前缀不匹配 GC 的 ^[0-9a-f]{12}$ 正则，数据集目录
被回收后口径记忆仍留存；compute_dataset_id 幂等保证同路径重载时命中。

约定：
- dataset_ref = 12 位 hex（compute_dataset_id 的 sha1[:12]）；
- 读取链 raw.h5ad → filtered.h5ad → processed.h5ad（QC 面向 raw）；
- X 支持 dense array 与 CSR/CSC group 两种 anndata 编码，分块读行，
  大行数时抽样（默认最多 5000 行）控制耗时；
- 任何异常/缺文件 → 返回 None（画像是可选项，绝不影响规划主链路）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"\b[0-9a-f]{12}\b")
_SAMPLE_ROWS_CAP = 5000
_CHUNK_ROWS = 2000
_PROFILES_DIR = "_profiles"
_STYLE_SPECIES = {"title": "mouse", "upper": "human"}


def extract_dataset_refs(text: str) -> list[str]:
    """从任务文本提取 12 位 hex dataset_ref（去重保序）。"""
    seen: list[str] = []
    for m in _REF_RE.finditer(text):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def detect_symbol_style(names: list[str]) -> str:
    """基因符号风格：title（Xkr4 鼠式）/ upper（XKR4 人式）/
    ensembl（ENSG*/ENSMUS*）/ mixed（不确定，不作 species 判据）。

    阈值 0.5：真实鼠源图谱 Title-case 命中率通常 >90%，人源全大写
    >95%；少数历史符号（B2M 全大写出现在鼠数据）不影响整体判定。
    """
    n = max(len(names), 1)
    title = sum(
        1 for g in names
        if g[:1].isalpha() and g[:1].isupper() and any(c.islower() for c in g))
    upper = sum(
        1 for g in names
        if any(c.isalpha() for c in g) and g == g.upper())
    ensembl = sum(1 for g in names if g.startswith(("ENSG", "ENSMUS")))
    if ensembl / n > 0.5:
        return "ensembl"
    if title / n > 0.5:
        return "title"
    if upper / n > 0.5:
        return "upper"
    return "mixed"


def _pick_h5ad(ds_dir: Path) -> Path | None:
    """读取链：raw（QC 输入）→ filtered → processed。"""
    for name in ("raw.h5ad", "filtered.h5ad", "processed.h5ad"):
        p = ds_dir / name
        if p.is_file():
            return p
    return None


def _gene_names(var: Any) -> list[str] | None:
    """var 组取基因名：优先 _index 数据集，其次 attrs['_index'] 指向列。"""

    if "_index" in var:
        raw = var["_index"][:]
    else:
        col = var.attrs.get("_index")
        if col and col in var:
            raw = var[col][:]
        else:
            return None
    return [g.decode() if isinstance(g, bytes) else str(g) for g in raw]


def _mt_mask(var: Any, names: list[str] | None) -> np.ndarray | None:
    """线粒体基因掩码：优先 var['mt'] 布尔列，否则按 MT-/mt- 前缀派生。"""
    if "mt" in var:
        try:
            return np.asarray(var["mt"][:]).astype(bool)
        except Exception:  # noqa: BLE001
            pass
    if names:
        return np.array([g.startswith(("MT-", "mt-")) for g in names])
    return None


def _row_stats_dense(
    x: Any, n_obs: int, mt_mask: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray | None]:
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
        if mt_mask is not None and mt_pct is not None:
            total = block.sum(axis=1)
            mt = block[:, mt_mask].sum(axis=1) if mt_mask.any() else 0.0
            mt_pct[off:off + len(idx)] = np.where(
                total > 0, mt / np.maximum(total, 1e-12) * 100.0, 0.0)
    return genes, mt_pct


def _row_stats_sparse(
    x: Any, n_obs: int, mt_mask: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray | None]:
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


def _obs_columns(obs: Any) -> dict[str, list[str]]:
    """obs 组列分类：categorical（h5py Group with categories）/ other。

    categorical 才是 celltype/group 参数的合法候选，数值列（n_genes 等
    QC 指标）单独归档供参考；异常列跳过，绝不抛出。
    """
    cat: list[str] = []
    other: list[str] = []
    for key in obs.keys():
        if key in ("index", "_index"):
            continue
        try:
            node = obs[key]
            enc = str(getattr(node, "attrs", {}).get("encoding-type", ""))
            if hasattr(node, "keys"):  # h5py Group=categorical（categories）
                cat.append(key)
            elif enc == "categorical":
                cat.append(key)
            else:
                other.append(key)
        except Exception:  # noqa: BLE001
            continue
    return {"categorical": cat[:40], "other": other[:40]}


def _profile_path(workspace_root: str, ref: str) -> Path:
    return Path(workspace_root) / _PROFILES_DIR / f"{ref}.json"


def _save_profile(workspace_root: str, profile: dict[str, Any]) -> None:
    """画像持久化（best-effort：失败仅告警，绝不影响主链路）。"""
    try:
        p = _profile_path(workspace_root, str(profile["ref"]))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("profile persist failed for %s", profile.get("ref"))


def cached_species_guess(workspace_root: str, ref: str) -> str | None:
    """读记忆库 species 猜测；无记忆时现算一次（顺带落盘）再读。

    非法入参（非 str/空串——单测 MagicMock 属性等）一律 None 不抛。
    """
    if (not isinstance(workspace_root, str) or not workspace_root
            or not isinstance(ref, str) or not ref):
        return None
    try:
        p = _profile_path(workspace_root, ref)
        if p.is_file():
            guess = json.loads(p.read_text(encoding="utf-8")).get(
                "species_guess")
            if guess in ("human", "mouse"):
                return str(guess)
    except Exception:  # noqa: BLE001
        pass
    try:
        profile = profile_dataset(workspace_root, ref)
    except Exception:  # noqa: BLE001
        return None
    if profile:
        guess = profile.get("species_guess")
        if guess in ("human", "mouse"):
            return str(guess)
    return None


def resolve_species(workspace_root: str, ref: str, species: str) -> str:
    """species 解析：显式传值透传；空串 → 记忆库猜测 → human 兜底。

    供六个 species 敏感工具 handler 兜底（Phase 61 教训：默认 human
    遇鼠源数据 → 资源库零交集在计算深处才报难解错误）。任何记忆库
    异常都不阻断工具执行（回退 human，由容器侧风格卫兵兜底）。
    """
    if species in ("human", "mouse"):
        return species
    try:
        return cached_species_guess(workspace_root, ref) or "human"
    except Exception:  # noqa: BLE001
        return "human"


def profile_dataset(workspace_root: str, ref: str) -> dict[str, Any] | None:
    """读 workspace/<ref>/ 下的 h5ad，返回画像 dict；失败/缺文件 → None。

    返回字段：ref/n_cells/n_genes/genes_per_cell{median,p90,max}/
    mt_pct{median,p95}（无线粒体基因时缺省）/source（用了哪个 h5ad）/
    gene_style+species_guess（符号风格→物种猜测）/obs_cols（可用列）。
    成功后持久化到 _profiles/<ref>.json（记忆库）。
    """
    if not isinstance(workspace_root, str) or not workspace_root:
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
            obs_cols = _obs_columns(f["obs"]) if "obs" in f else {}
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
        if names:
            style = detect_symbol_style(names)
            out["gene_style"] = style
            guess = _STYLE_SPECIES.get(style)
            if guess:
                out["species_guess"] = guess
        if obs_cols:
            out["obs_cols"] = obs_cols
        _save_profile(workspace_root, out)
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
        if p.get("species_guess"):
            seg += (f"；基因符号风格={p.get('gene_style')} → 物种应为 "
                    f"{p['species_guess']}（species 参数必须与此一致）")
        elif p.get("gene_style"):
            seg += f"；基因符号风格={p['gene_style']}（物种不确定）"
        cols = p.get("obs_cols", {})
        if cols.get("categorical"):
            seg += (f"；可用分组列：{', '.join(cols['categorical'][:12])}"
                    + ("…" if len(cols["categorical"]) > 12 else ""))
        lines.append(seg)
    lines.append(
        "sc_qc 的 min_genes 须明显低于 median（建议 ≤ median/2），"
        "max_mt_pct 参考 p95 上调；严禁套用默认值不看数据规模。"
        "物种敏感工具（cellchat/metabolism/scenic/commot）的 species "
        "按画像猜测传，鼠源符号（Xkr4 式 Title-case）=mouse、全大写=human。")
    return "\n".join(lines)


_DATASET_DIR_RE = re.compile(r"^[0-9a-f]{12}")


def _quick_meta(ds_dir: Path) -> dict[str, Any] | None:
    """轻量目录画像：只读 h5ad 元数据（X shape + obs 列），毫秒级。

    读取链 processed → filtered → raw（面向"继续分析"场景，processed
    才是 sc_plot 等下游的输入）；任何异常 → None（清单是可选项）。
    """
    h5 = None
    for name in ("processed.h5ad", "filtered.h5ad", "raw.h5ad"):
        p = ds_dir / name
        if p.is_file():
            h5 = p
            break
    if h5 is None:
        return None
    try:
        import h5py

        with h5py.File(h5, "r") as f:
            x = f.get("X")
            if x is None:
                return None
            if isinstance(x, h5py.Dataset):
                shape = tuple(x.shape)
            else:
                shape = tuple(x.attrs.get("shape", ()))
            if len(shape) != 2:
                return None
            obs_cols = _obs_columns(f["obs"]) if "obs" in f else {}
        return {"source": h5.name, "n_cells": int(shape[0]),
                "n_genes": int(shape[1]), "obs_cols": obs_cols}
    except Exception:  # noqa: BLE001
        return None


def build_workspace_context(workspace_root: str,
                            data_roots: list[str] | tuple[str, ...] | str,
                            *, cap: int = 8) -> str:
    """workspace 已有数据集清单文本段（planner 注入用，2026-09-29 缺口）。

    动因（真机）：整合任务完成后的追问（"把注释 UMAP 画出来/发给我"）
    不带 dataset_ref，模型不知道工作区里已有什么，只能幻觉 run_python
    去 /data、D:/sc_data 等错误根目录发现 → 空结果收尾。本清单把已有
    dataset_ref（目录名自带血缘，如 {id}_{id}_merged_harmony）+ 规模 +
    可用分组列 + 已产出图注入 session_context，让追问直接引用续作。

    只读元数据（X shape/obs 列键），不读表达矩阵，毫秒级；cap 防爆。
    workspace_root 空/无数据集 → 空串（调用方不追加）。
    """
    if not isinstance(workspace_root, str) or not workspace_root:
        return ""
    root = Path(workspace_root)
    if not root.is_dir():
        return ""
    try:
        dirs = sorted(
            (d for d in root.iterdir()
             if d.is_dir() and _DATASET_DIR_RE.match(d.name)),
            key=lambda d: d.stat().st_mtime, reverse=True)[:cap]
    except Exception:  # noqa: BLE001
        return ""
    if not dirs:
        return ""

    lines = [
        "当前工作区已有数据集（继续分析直接把目录名作 dataset_ref 传给 "
        "sc_* 工具；严禁重新发现/重新加载已有数据）：",
    ]
    for d in dirs:
        meta = _quick_meta(d)
        if meta is None:
            lines.append(f"- {d.name}（无可用 h5ad）")
            continue
        seg = (f"- {d.name}（{meta['source']}）：{meta['n_cells']} 细胞 × "
               f"{meta['n_genes']} 基因")
        cat = meta["obs_cols"].get("categorical", [])
        if cat:
            seg += (f"；分组列：{', '.join(cat[:10])}"
                    + ("…" if len(cat) > 10 else ""))
        pngs = sorted(p.name for p in d.glob("*.png"))
        if pngs:
            seg += (f"；已产出图：{', '.join(pngs[:6])}"
                    + ("…" if len(pngs) > 6 else ""))
        lines.append(seg)

    if isinstance(data_roots, str):
        roots = [data_roots] if data_roots else []
    else:
        roots = [str(r) for r in data_roots if str(r).strip()]
    if roots:
        lines.append(
            "本地数据根目录（仅在需要发现【新】数据文件时用 run_python "
            "在这些路径下找，别处没有）："
            + "；".join(roots))
    lines.append(
        "画已有数据的注释/分组 UMAP 用 sc_plot kind=umap_obs + obs_cols="
        "分组列；obs 分组列（如细胞类型注释）随 sc_merge/sc_integrate "
        "自动保留，无需先『加入注释』。已产出图可用 sc_plot 复画后自动 "
        "IM 回传/随报告写入绑定文档。")
    return "\n".join(lines)
