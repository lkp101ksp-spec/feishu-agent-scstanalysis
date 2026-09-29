"""sc_merge：多数据集细胞横向拼接 → 新 dataset_ref（2026-09-29 真机缺口）。

stdin: {"dataset_ids": ["<id>","<id>",...], "batch_col": "dataset"}

背景：PDAC 任务（sc + sn 两库按 orig.ident 整合）暴露工具图缺口——
sc_integrate 只吃单数据集，此前没有任何工具能把两个 dataset 拼成一个，
planner 只能退化成"只整合第一个库"。本工具补上该缺口。

行为：每个输入按 load_adata(any) 回退链读（filtered 优先）→
ad.concat(axis=0, join="outer", 稀疏保零） → obs 增 batch_col 列=来源
dataset_id（原 orig.ident 等批次列原样保留，下游 sc_integrate 可继续
按 orig.ident 整合）→ 产物 WS/{new_id}/raw.h5ad（new_id =
{id1}_{id2}_merged，幂等覆盖）。
"""
from __future__ import annotations

from typing import Any

from common import WS_ROOT, emit, fail, load_adata, read_args, run


def main() -> None:
    """主流程：逐个读入 → 校验 ≥2 → concat → 落 raw.h5ad → emit 概要。"""
    import anndata as ad

    args = read_args()
    ids = [str(s).strip() for s in args.get("dataset_ids", []) if str(s).strip()]
    batch_col = str(args.get("batch_col", "dataset")).strip() or "dataset"
    if len(ids) < 2:
        fail("SC_MERGE_NEED_TWO",
             f"merge needs >=2 dataset_ids, got {len(ids)}")
        raise SystemExit(1)

    adatas: list[Any] = []
    per_ds: dict[str, int] = {}
    for ds in ids:
        a = load_adata({"dataset_id": ds, "file": "any"})
        per_ds[ds] = int(a.n_obs)
        adatas.append(a)

    # index_unique 防跨库 barcode 撞名（10x  barcode 在库间天然重复）
    merged = ad.concat(adatas, axis=0, join="outer",
                       label=batch_col, keys=ids, index_unique="-")
    new_id = "_".join(ids) + "_merged"
    ds_dir = WS_ROOT / new_id
    ds_dir.mkdir(parents=True, exist_ok=True)
    merged.write_h5ad(ds_dir / "raw.h5ad")

    emit({
        "ok": True,
        "dataset_ref": new_id,
        "n_cells": int(merged.n_obs),
        "n_genes": int(merged.n_vars),
        "batch_col": batch_col,
        "cells_per_dataset": per_ds,
        "workspace": str(ds_dir),
    })


if __name__ == "__main__":
    run(main)
