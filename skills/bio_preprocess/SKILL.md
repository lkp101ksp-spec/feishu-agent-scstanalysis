---
name: bio_preprocess
description: 单细胞标准预处理（QC 过滤 + 归一化 + HVG + PCA + UMAP + leiden 聚类）
---
对 h5ad 做标准单细胞预处理，CPU 栈（scanpy）：

1. QC 过滤：`filter_cells(min_genes=600)` + `filter_genes(min_cells=3)` + mt_pct<20
2. 归一化：`normalize_total(target_sum=1e4)` + `log1p`
3. HVG：`highly_variable_genes(n_top_genes=2000, flavor="seurat")`，`adata.raw` 落全基因快照
4. 降维：`scale(max_value=10)` → `pca(n_comps=50, svd_solver="arpack")` → `neighbors(n_neighbors=15)` → `umap`
5. 聚类：`leiden(resolution=1.0, flavor="igraph")`

输入：h5ad 路径（含 raw counts）。
输出：processed h5ad（obs 含 leiden 列，obsm 含 UMAP/PCA，.raw 归一化快照）+ 聚类数/簇大小分布。
