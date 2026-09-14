"""sc_meta：元数据编辑（Phase 35，对齐 toolsv1 server_meta_settings/file_io）。

stdin: {"dataset_id": ..., "op": "merge_csv"|"map_values"|"rename_col", ...}
- merge_csv: {"csv_path": "<rel-under-/data>", "key_col": "sample"}
  csv 首列=key，其余列按 key 并入 obs；与现有列同名时加 _csv 后缀
  （防覆盖，conflict 列表在 note 返回）
- map_values: {"col": "leiden", "mapping": {"0": "T cell"}, "out_col": "..."}
  未映射取值保留原值；out_col 缺省 = {col}_mapped
- rename_col: {"old": "orig.ident", "new": "sample"}
写回 processed.h5ad 原地保存。merge_csv 的 csv 走 /data 挂载
（handler 层 resolve_data_path 白名单）。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from common import DATA_ROOT, WS_ROOT, emit, load_adata, read_args, run


def main() -> None:
    """主流程：按 op 分派三种编辑，结果原地写回 processed.h5ad。"""
    args = read_args()
    op = str(args.get("op", "")).strip()
    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    n_cells = adata.n_obs
    note = ""

    if op == "merge_csv":
        csv_path = str(args.get("csv_path", "")).strip()
        key_col = str(args.get("key_col", "")).strip()
        if not (csv_path and key_col):
            raise ValueError("merge_csv needs csv_path and key_col")
        if key_col not in adata.obs:
            raise ValueError(f"key column {key_col!r} not in obs; "
                             f"existing: {list(adata.obs.columns)[:20]}")
        p = (DATA_ROOT / csv_path).resolve()
        if DATA_ROOT not in p.parents:
            raise ValueError(f"invalid csv_path: {csv_path!r}")
        table = pd.read_csv(p, sep=None, engine="python", index_col=0)
        table.index = table.index.astype(str)
        key = adata.obs[key_col].astype(str)
        conflicts, added = [], []
        for c in table.columns:
            target = str(c)
            if target in adata.obs:
                target = f"{c}_csv"
                conflicts.append(str(c))
            mapped = key.map(table[str(c)].astype(str))
            n_na = int(mapped.isna().sum())
            adata.obs[target] = mapped
            added.append({"col": target, "unmatched_cells": n_na})
        if conflicts:
            note = f"columns renamed with _csv suffix: {conflicts}"
        changed = [a["col"] for a in added]
        detail: Any = added

    elif op == "map_values":
        col = str(args.get("col", "")).strip()
        mapping = args.get("mapping")
        out_col = str(args.get("out_col", "")).strip() or f"{col}_mapped"
        if not col or not isinstance(mapping, dict) or not mapping:
            raise ValueError("map_values needs col and mapping dict, e.g. "
                             '{"col": "leiden", "mapping": {"0": "T cell"}}')
        if col not in adata.obs:
            raise ValueError(f"column {col!r} not in obs; "
                             f"existing: {list(adata.obs.columns)[:20]}")
        src = adata.obs[col].astype(str)
        mapped = src.map({str(k): str(v) for k, v in mapping.items()})
        adata.obs[out_col] = mapped.fillna(src)
        unmapped = sorted(set(src) - set(str(k) for k in mapping))
        if unmapped:
            note = f"unmapped values kept as-is: {unmapped[:20]}"
        changed = [out_col]
        detail = {"values": adata.obs[out_col].value_counts()
                  .head(20).to_dict()}

    elif op == "rename_col":
        old = str(args.get("old", "")).strip()
        new = str(args.get("new", "")).strip()
        if not (old and new):
            raise ValueError("rename_col needs old and new")
        if old not in adata.obs:
            raise ValueError(f"column {old!r} not in obs; "
                             f"existing: {list(adata.obs.columns)[:20]}")
        if new in adata.obs:
            raise ValueError(f"target column {new!r} already exists")
        adata.obs[new] = adata.obs.pop(old)
        changed = [new]
        detail = {"renamed": f"{old} -> {new}"}

    elif op == "list_cols":
        # obs 分组列发现（Phase 59，只读不写回）：类别列（2≤nunique≤100）
        # 供 celltype_col/group_col 选列 + 轨迹产物列模式标注
        traj_names = {"palantir_branch", "lineage_branch",
                      "slingshot_lineage"}
        group_cols, traj_cols = [], []
        for c in adata.obs.columns:
            s = adata.obs[c]
            nun = int(s.nunique(dropna=True))
            is_cat = (isinstance(s.dtype, pd.CategoricalDtype)
                      or s.dtype == object) and 2 <= nun <= 100
            if is_cat:
                group_cols.append({"col": str(c), "n_levels": nun})
            if str(c) in traj_names or str(c).endswith("_pseudotime"):
                traj_cols.append(str(c))
        changed = []
        detail = {"group_cols": group_cols,
                  "trajectory_cols": traj_cols}

    else:
        raise ValueError(f"op must be merge_csv/map_values/rename_col/"
                         f"list_cols, got {op!r}")

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    if op != "list_cols":  # list_cols 只读不落盘
        adata.write(h5ad_path)
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "op": op,
        "changed_cols": changed,
        "n_cells": n_cells,
        "detail": detail,
        "note": note,
        "saved": str(h5ad_path),
    })


if __name__ == "__main__":
    run(main)
