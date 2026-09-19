"""sc_tcr：免疫组库重建 + Startrac 指数（Phase 70，L3 工具）。

stdin 契约（仅 contig_files 必填）：
  {"contig_files": [{"file": "vdj/p1_tumor.csv.gz",
                      "patient": "P1", "tissue": "Tumor"}, ...],
   "tissue1": "", "tissue2": "", "dataset_ref": ""}

数据流（spec 2026-09-19-sc-tcr-design.md §2）：逐文件读 Cell Ranger
filtered_contig_annotations.csv(.gz)（file 为 /data 挂载内相对路径，
handler 侧白名单校验）→ 六重过滤（is_cell/high_confidence/productive/
链∈{TRA,TRB}/raw_clonotype_id 非空 → 样本内 TRA+TRB 双链齐 →
barcode 多 clonotype 剔除）→ 跨组织 clonotype
`patient::sorted_unique(cdr3s_nt)`（同患者跨组织同序列合一、患者间
前缀隔离）→ 全局 clone_size 分级 n>=3/n=2/n=1 → Startrac expa（逐
患者×组织）+ pairwise_migr（tissue1↔tissue2，未归一化熵原文口径）。

可选写回：dataset_ref 给出时按 barcode 对齐 processed.h5ad obs 增
tcr_clonotype/tcr_clone_size/tcr_size_class 三列（对齐率 <50% 拒收
TCR_ALIGN_FAILED 防错配；裸 barcode 与全 cell_id 双口径取高命中）。

产物（落 /ws/{new_id}/）：tcr_cells.csv / tcr_indices.csv /
tcr_overview.png（rank-frequency + 患者×分级堆叠 + 指数条形三联）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import DATA_ROOT, WS_ROOT, emit, fail, load_adata, read_args, run

REQUIRED_COLS = ["barcode", "chain", "cdr3_nt", "raw_clonotype_id",
                 "is_cell", "high_confidence", "productive"]


def _die(code: str, msg: str) -> None:
    """结构化失败：common.fail 输出 JSON 后退出（错误码见 spec §7）。"""
    fail(code, msg)
    raise SystemExit(1)


def _as_bool(s: pd.Series) -> pd.Series:
    """is_cell 等布尔列的 str/bool 双形态归一（Cell Ranger 输出 True/False
    字符串，read_csv 常推断为 bool，防御两种都吃）。"""
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])


def _cell_id(patient: str, tissue: str, barcode: str) -> str:
    """全局唯一细胞 ID：双下划线三段（跨样本 10x barcode 撞码是常态，
    样本前缀消歧）。"""
    return f"{patient}__{tissue}__{barcode}"


def _clonotype_id(patient: str, cdr3s_nt: set[str]) -> str:
    """跨组织 clonotype：patient::canonical(cdr3s_nt)——排序去重回拼，
    同患者跨组织同配对序列合一、患者间前缀隔离（勿用 Cell Ranger
    raw_clonotype_id：各样本独立编号无跨样本意义）。"""
    return f"{patient}::{' ;'.join(sorted(cdr3s_nt))}"


def startrac_expa(clone_counts: list[int]) -> float:
    """扩张指数：1 - Shannon 熵 / log2(K)，K=克隆数（探针真值对拍
    P1[6,4,1,1]=0.1870927082）。"""
    total = sum(clone_counts)
    if len(clone_counts) < 2 or total == 0:
        return float("nan")
    p = [c / total for c in clone_counts]
    h = -sum(x * np.log2(x) for x in p)
    return float(1.0 - h / np.log2(len(p)))


def startrac_migr(cells: pd.DataFrame, patient: str,
                   t1: str, t2: str) -> float:
    """迁移指数（t1↔t2）：Σ (clone_size/total) × H_两组织——未归一化熵
    （Startrac 原文口径；探针真值 P1=0.5509775004/P2=0.4）。"""
    sub = cells[(cells["patient"] == patient)
                & cells["tissue"].isin([t1, t2])]
    total = len(sub)
    if total == 0:
        return float("nan")
    val = 0.0
    for _, g in sub.groupby("clonotype_id"):
        n1 = int((g["tissue"] == t1).sum())
        n2 = int((g["tissue"] == t2).sum())
        n = n1 + n2
        h = 0.0
        for k in (n1, n2):
            if k:
                h -= (k / n) * np.log2(k / n)
        val += (n / total) * h
    return float(val)


def _new_id(patients: list[str], n_files: int) -> str:
    """产物目录名：tcr_{患者集合}-{文件数}——\\W 清洗 + 40 帽。"""
    stem = "-".join(sorted(set(patients)))
    return re.sub(r"\W+", "_", f"tcr_{stem}_{n_files}")[:40]


def _filter_one_file(dt: pd.DataFrame, patient: str, tissue: str
                     ) -> tuple[pd.DataFrame, dict[str, int]]:
    """单样本表六重过滤 → 有效细胞行 + 逐级剔除计数（emit note 素材）。

    双链齐/ambiguous 检查限定样本内（跨样本 barcode 撞码是 10x 编码
    空间所限，非生物学事件，由 cell_id 样本前缀消歧）。
    """
    stats = {"rows_total": len(dt), "not_cell": 0, "not_conf": 0,
             "not_prod": 0, "bad_chain": 0, "no_clonotype": 0,
             "single_chain": 0, "ambiguous": 0, "cells_kept": 0}
    for col in REQUIRED_COLS:
        if col not in dt.columns:
            _die("INVALID_INPUT",
                 f"contig file missing required column {col!r}; "
                 f"expected Cell Ranger filtered_contig_annotations "
                 f"schema: {REQUIRED_COLS}")
    m_cell = _as_bool(dt["is_cell"])
    m_conf = _as_bool(dt["high_confidence"])
    m_prod = _as_bool(dt["productive"])
    m_chain = dt["chain"].isin(["TRA", "TRB"])
    m_ct = dt["raw_clonotype_id"].astype(str).str.strip().ne("")
    stats["not_cell"] = int((~m_cell).sum())
    stats["not_conf"] = int((m_cell & ~m_conf).sum())
    stats["not_prod"] = int((m_cell & m_conf & ~m_prod).sum())
    stats["bad_chain"] = int((m_cell & m_conf & m_prod & ~m_chain).sum())
    stats["no_clonotype"] = int(
        (m_cell & m_conf & m_prod & m_chain & ~m_ct).sum())
    keep = dt[m_cell & m_conf & m_prod & m_chain & m_ct]
    rows: list[dict[str, Any]] = []
    for bc, g in keep.groupby("barcode", sort=True):
        chains = set(g["chain"].astype(str))
        cts = set(g["raw_clonotype_id"].astype(str))
        if not {"TRA", "TRB"} <= chains:
            stats["single_chain"] += 1
            continue
        if len(cts) > 1:
            stats["ambiguous"] += 1
            continue
        rows.append({"cell_id": _cell_id(patient, tissue, str(bc)),
                     "barcode": str(bc),
                     "patient": patient, "tissue": tissue,
                     "cdr3s_nt": set(g["cdr3_nt"].astype(str))})
    stats["cells_kept"] = len(rows)
    return pd.DataFrame(rows), stats


def _write_back(cells: pd.DataFrame, dataset_ref: str
                ) -> tuple[float, int]:
    """克隆三列写回 processed.h5ad obs：裸 barcode 与全 cell_id 双口径
    对齐取高命中（GEX 合并数据集 index 可能带样本前缀）；裸 barcode
    跨样本歧义者剔除。返回 (对齐率, 命中细胞数)。"""
    adata = load_adata({"dataset_id": dataset_ref, "file": "processed"})
    obs_idx = pd.Index(adata.obs_names.astype(str))

    # 口径 A：全 cell_id（要求 GEX index 恰带 patient__tissue__ 前缀）
    hit_full = obs_idx.isin(cells["cell_id"])
    rate_full = float(hit_full.mean()) if len(obs_idx) else 0.0

    # 口径 B：裸 barcode（歧义 barcode——同 barcode 多 cell_id——不参与）
    bc_map: dict[str, str] = {}
    bc_counts = cells["barcode"].value_counts()
    for _, r in cells.iterrows():
        if bc_counts[r["barcode"]] == 1:
            bc_map[r["barcode"]] = r["clonotype_id"]
    hit_bare = obs_idx.isin(bc_map.keys())
    rate_bare = float(hit_bare.mean()) if len(obs_idx) else 0.0

    if rate_bare > rate_full:
        key_map, rate = bc_map, rate_bare
    else:
        rowmap = cells.drop_duplicates("cell_id").set_index("cell_id")
        key_map, rate = rowmap["clonotype_id"].to_dict(), rate_full

    if rate < 0.5:
        _die("TCR_ALIGN_FAILED",
             f"barcode alignment rate {rate:.1%} < 50% — GEX "
             f"({dataset_ref}) 与 contig 疑非同源 run，拒防错配")
    idx_clones: list[Any] = []
    for name in adata.obs_names:
        idx_clones.append(key_map.get(str(name)))
    ser = pd.Series(idx_clones, index=adata.obs_names, dtype="object")
    hit = ser.notna()
    size = (cells.drop_duplicates("clonotype_id")
            .set_index("clonotype_id")["clone_size"].to_dict())
    cls = (cells.drop_duplicates("clonotype_id")
           .set_index("clonotype_id")["size_class"].astype(str).to_dict())
    adata.obs["tcr_clonotype"] = ser.where(hit, "")
    adata.obs["tcr_clone_size"] = pd.Series(
        [size.get(c, 0) if c else 0 for c in ser], index=adata.obs_names)
    adata.obs["tcr_size_class"] = pd.Series(
        [cls.get(c, "") if c else "" for c in ser], index=adata.obs_names)
    adata.write_h5ad(WS_ROOT / dataset_ref / "processed.h5ad")
    return rate, int(hit.sum())


def _plot_overview(cells: pd.DataFrame, expa: dict[str, float],
                   migr: dict[str, float], tissue_pair: str, out: Path
                   ) -> None:
    """三联总览：克隆大小 rank-frequency（log-log）/ 患者×分级堆叠 /
    expa·migr 条形。"""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), dpi=150)
    sizes = cells.drop_duplicates("clonotype_id")["clone_size"]
    sizes = sizes.sort_values(ascending=False).to_numpy(dtype=float)
    ax = axes[0]
    ax.loglog(range(1, len(sizes) + 1), sizes, marker=".", ls="none",
              markersize=4)
    ax.set_xlabel("clonotype rank")
    ax.set_ylabel("clone size (cells)")
    ax.set_title("Clonotype size distribution (log-log)")
    ax = axes[1]
    ct = pd.crosstab(cells.drop_duplicates("cell_id")["patient"],
                     cells.drop_duplicates("cell_id")["size_class"])
    order = [c for c in ["n>=3", "n=2", "n=1"] if c in ct.columns]
    ct = ct.reindex(columns=order, fill_value=0)
    bottom = np.zeros(len(ct))
    for i, c in enumerate(order):
        ax.bar(ct.index, ct[c], bottom=bottom, label=c,
               color=plt.get_cmap("tab10")(i), width=0.6)
        bottom += ct[c].to_numpy(dtype=float)
    ax.set_ylabel("cells")
    ax.set_title("Cells by patient × clone size class")
    ax.legend(fontsize=7)
    ax = axes[2]
    labels: list[str] = []
    vals: list[float] = []
    for k in sorted(expa):
        if np.isfinite(expa[k]):
            labels.append(f"expa\n{k}")
            vals.append(expa[k])
    for k in sorted(migr):
        if np.isfinite(migr[k]):
            labels.append(f"migr\n{k}")
            vals.append(migr[k])
    ax.bar(range(len(vals)), vals, color="steelblue", width=0.6)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels, fontsize=6, rotation=45, ha="right")
    ax.set_title(f"Startrac indices (migr: {tissue_pair})")
    ax.axhline(0, color="gray", lw=0.5)
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    """主流程：读参 → 逐文件过滤 → clonotype 重建 → 指数 → 产物。"""
    args = read_args()
    files_in = args.get("contig_files")
    if (not isinstance(files_in, list) or not files_in
            or not all(isinstance(f, dict) for f in files_in)):
        _die("INVALID_INPUT",
             "contig_files must be a non-empty list of "
             "{file, patient, tissue} objects")
    contig_files: list[dict[str, str]] = []
    for f in files_in:
        try:
            contig_files.append({"file": str(f["file"]),
                                 "patient": str(f["patient"]).strip(),
                                 "tissue": str(f["tissue"]).strip()})
        except KeyError as e:
            _die("INVALID_INPUT",
                 f"contig_files element missing field {e}; expected "
                 "{{file, patient, tissue}}")
    for c in contig_files:
        if not c["patient"] or not c["tissue"]:
            _die("INVALID_INPUT",
                 f"patient/tissue must be non-empty strings (got {c!r})")

    tissue1 = str(args.get("tissue1", "")).strip()
    tissue2 = str(args.get("tissue2", "")).strip()
    dataset_ref = str(args.get("dataset_ref", "")).strip()

    frames: list[pd.DataFrame] = []
    all_stats: dict[str, int] = {}
    for c in contig_files:
        path = DATA_ROOT / c["file"]
        if not path.exists():
            _die("INVALID_INPUT",
                 f"contig file not found under data mount: {c['file']!r}")
        dt = pd.read_csv(path)  # read_csv 原生支持 .gz
        rows, stats = _filter_one_file(dt, c["patient"], c["tissue"])
        frames.append(rows)
        for k, v in stats.items():
            all_stats[k] = all_stats.get(k, 0) + v
    cells = pd.concat(frames, ignore_index=True)
    if cells.empty:
        _die("TCR_NO_VALID_CELLS",
             f"no cells survived filtering (stats: {all_stats}) — "
             "check is_cell/high_confidence/productive/TRA+TRB pairing")
    cells["clonotype_id"] = [
        _clonotype_id(p, s) for p, s in zip(cells["patient"],
                                            cells["cdr3s_nt"])]
    cells["clone_size"] = (cells.groupby("clonotype_id")["cell_id"]
                           .transform("count"))
    cells["size_class"] = pd.cut(cells["clone_size"], [0, 1, 2, np.inf],
                                 labels=["n=1", "n=2", "n>=3"]).astype(str)
    cells = cells.drop(columns=["cdr3s_nt"]).sort_values(
        ["patient", "tissue", "clonotype_id"], ignore_index=True)

    patients = sorted(cells["patient"].unique())
    tissues = sorted(cells["tissue"].unique())
    for t in (tissue1, tissue2):
        if t and t not in tissues:
            _die("INVALID_INPUT",
                 f"tissue {t!r} not in observed tissues {tissues}")
    if not tissue1 or not tissue2:
        if len(tissues) >= 2:
            tissue1, tissue2 = tissues[0], tissues[1]
        else:
            tissue1 = tissue1 or (tissues[0] if tissues else "")
            tissue2 = tissue2 or tissue1

    expa: dict[str, float] = {}
    for p in patients:
        for t in tissues:
            cnt = (cells[(cells["patient"] == p) & (cells["tissue"] == t)]
                   ["clonotype_id"].value_counts().tolist())
            if cnt:
                expa[f"{p}|{t}"] = startrac_expa(cnt)
    migr: dict[str, float] | None
    if len(tissues) >= 2 and tissue1 != tissue2:
        migr = {p: startrac_migr(cells, p, tissue1, tissue2)
                for p in patients}
    else:
        migr = None

    new_id = _new_id(patients, len(contig_files))
    ds_dir = WS_ROOT / new_id
    ds_dir.mkdir(parents=True, exist_ok=True)
    cells_csv = ds_dir / "tcr_cells.csv"
    cells.to_csv(cells_csv, index=False)
    idx_rows = [{"index_type": "expa", "key": k, "value": v}
                for k, v in expa.items()]
    idx_rows += ({"index_type": "migr", "key": k, "value": v,
                  "tissue_pair": f"{tissue1}<->{tissue2}"}
                 for k, v in (migr or {}).items())
    idx_csv = ds_dir / "tcr_indices.csv"
    pd.DataFrame(idx_rows).to_csv(idx_csv, index=False)
    png = ds_dir / "tcr_overview.png"
    _plot_overview(cells, expa, migr or {},
                   f"{tissue1}<->{tissue2}" if migr else "n/a", png)

    wrote_back = False
    align_rate = None
    n_aligned = 0
    if dataset_ref:
        align_rate, n_aligned = _write_back(cells, dataset_ref)
        wrote_back = True

    note = (f"filter stats: {all_stats}; clonotype scope: per-patient "
            f"cross-tissue (canonical cdr3s_nt)"
            + ("" if migr else
               f"; migr unavailable (single tissue {tissues}), expa only"))
    emit({"ok": True, "dataset_ref": new_id,
          "n_files": len(contig_files), "n_patients": len(patients),
          "n_tissues": len(tissues),
          "n_rows_total": all_stats.get("rows_total", 0),
          "n_cells_kept": int(len(cells)),
          "n_clonotypes": int(cells["clonotype_id"].nunique()),
          "size_class_counts": cells["size_class"].value_counts()
          .to_dict(),
          "expa": expa, "migr": migr, "tissue_pair":
          f"{tissue1}<->{tissue2}" if migr else None,
          "tcr_cells_csv": str(cells_csv), "tcr_indices_csv": str(idx_csv),
          "overview_png": str(png), "wrote_back": wrote_back,
          "align_rate": align_rate, "n_aligned": n_aligned, "note": note})


if __name__ == "__main__":
    run(main)
