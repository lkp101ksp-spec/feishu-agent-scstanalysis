"""单细胞标准预处理：QC + 归一化 + HVG + PCA + UMAP + leiden（CPU 栈 scanpy）。"""
import argparse
import json
from pathlib import Path

import scanpy as sc


def main() -> None:
    """按 scanpy 标准流程预处理 h5ad，落盘 processed 文件与 leiden 聚类。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_path", required=True, help="输入 h5ad（raw counts）")
    ap.add_argument("--output_path", required=True, help="输出 processed h5ad")
    ap.add_argument("--min_genes", type=int, default=600)
    ap.add_argument("--min_cells", type=int, default=3)
    ap.add_argument("--max_mt_pct", type=float, default=20.0)
    ap.add_argument("--n_top_hvg", type=int, default=2000)
    ap.add_argument("--n_pcs", type=int, default=50)
    ap.add_argument("--n_neighbors", type=int, default=15)
    ap.add_argument("--resolution", type=float, default=1.0)
    args = ap.parse_args()

    inp = Path(args.input_path)
    if not inp.is_file():
        print(json.dumps({"ok": False, "error": f"input not found: {inp}"}))
        return

    adata = sc.read_h5ad(inp)
    n_before = int(adata.n_obs)

    # mt 比例（基因名 mt- 前缀）
    adata.var["mt"] = adata.var_names.str.startswith("mt-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)

    # QC 过滤
    sc.pp.filter_cells(adata, min_genes=args.min_genes)
    sc.pp.filter_genes(adata, min_cells=args.min_cells)
    adata = adata[adata.obs["pct_counts_mt"] < args.max_mt_pct].copy()

    # 归一化 + log
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    # HVG + raw 快照
    sc.pp.highly_variable_genes(adata, n_top_genes=args.n_top_hvg, flavor="seurat")
    adata.raw = adata
    adata = adata[:, adata.var["highly_variable"]].copy()

    # 降维
    n_comps = min(args.n_pcs, adata.n_vars - 1, adata.n_obs - 1)
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=n_comps, svd_solver="arpack")
    sc.pp.neighbors(adata, n_neighbors=args.n_neighbors)
    sc.tl.umap(adata)
    sc.tl.leiden(adata, resolution=args.resolution, flavor="igraph",
                 n_iterations=2, directed=False)

    out = Path(args.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)

    cluster_sizes = adata.obs["leiden"].value_counts().to_dict()
    print(json.dumps({
        "ok": True,
        "output_path": str(out),
        "n_cells_before": n_before,
        "n_cells_after": int(adata.n_obs),
        "n_genes_after": int(adata.n_vars),
        "n_clusters": int(adata.obs["leiden"].nunique()),
        "cluster_sizes": {str(k): int(v) for k, v in cluster_sizes.items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
