---
name: bio_10x_merge
description: 10x Genomics 多样本合并（filtered_feature_bc_matrix 三件套目录 → 单 h5ad）
---
把多个 10x 样本的 `filtered_feature_bc_matrix` 目录（各含 barcodes.tsv.gz / features.tsv.gz / matrix.mtx.gz）合并为一个 AnnData h5ad。

输入：一个父目录，其下每个子目录是一个样本（目录名即 batch 标签）。
处理：
1. 逐样本 `scanpy.read_10x_mtx(var_names="gene_symbols", make_unique=True)`
2. `ad.concat(axis=0, join="outer", label="batch", keys=样本目录名)`
3. obs_names 与 var_names 去重（cudf 对重复索引敏感）
4. 落盘 h5ad

输出：合并后 h5ad 路径 + 细胞数/基因数/batch 列分布。
