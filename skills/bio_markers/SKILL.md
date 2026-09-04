---
name: bio_markers
description: 单细胞 markers 提取（按 leiden 簇做 wilcoxon 差异分析，出每簇 top markers）
---
对 processed h5ad（须含 leiden 列与 .raw 归一化快照）按簇提取 markers：

1. `sc.tl.rank_genes_groups(groupby="leiden", method="wilcoxon", use_raw=True)`
   （use_raw=True 用归一化 log 全基因快照，结果更稳）
2. 每簇取前 top_n 个基因，提取 names/scores/logfoldchanges
3. 附 dotplot 图

输入：processed h5ad（bio_preprocess 产出）。
输出：每簇 top markers 列表（gene/score/log2fc）+ dotplot 路径 + 簇数。
