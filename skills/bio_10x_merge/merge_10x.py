"""10x 多样本合并：父目录下逐样本 read_10x_mtx → concat → 去重 → h5ad。"""
import argparse
import json
from pathlib import Path

import anndata as ad
import scanpy as sc


def main() -> None:
    """逐样本读取 10x 三件套目录，outer 合并且加 batch 列，去重后落盘。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", required=True, help="父目录（每个子目录一个样本）")
    ap.add_argument("--output_path", required=True, help="输出 h5ad 路径")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"input_dir not found: {input_dir}"}))
        return

    # 逐样本读取：只取含 matrix.mtx* 的目录（兼容嵌套 filtered_feature_bc_matrix）
    adatas = []
    for sub in sorted(input_dir.iterdir()):
        if not sub.is_dir():
            continue
        mtx_dir = sub / "filtered_feature_bc_matrix"
        target = mtx_dir if mtx_dir.is_dir() else sub
        if not any(target.glob("matrix.mtx*")):
            continue
        a = sc.read_10x_mtx(str(target), var_names="gene_symbols", make_unique=True)
        a.obs["batch"] = sub.name
        adatas.append(a)

    if not adatas:
        print(json.dumps({"ok": False, "error": "no 10x samples found"}))
        return

    merged = ad.concat(adatas, axis=0, join="outer", label="batch",
                       keys=[a.obs["batch"].iloc[0] for a in adatas])
    merged.obs_names_make_unique()
    merged.var_names_make_unique()

    out = Path(args.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.write_h5ad(out)

    batch_counts = merged.obs["batch"].value_counts().to_dict()
    print(json.dumps({
        "ok": True,
        "output_path": str(out),
        "n_cells": int(merged.n_obs),
        "n_genes": int(merged.n_vars),
        "batch_counts": batch_counts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
