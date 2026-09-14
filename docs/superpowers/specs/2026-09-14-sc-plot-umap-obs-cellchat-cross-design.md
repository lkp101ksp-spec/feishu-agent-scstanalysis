# Phase 58 设计：sc_plot umap_obs + lineage_branch × sc_cellchat 联用

日期：2026-09-14｜状态：已批准（用户"按这个方案来"）

## §0 背景

Phase 57 把 slingshot_lineage/lineage_branch 写回 obs 后发现：
sc_plot 工具仅支持基因着色（violin/umap_gene），obs 列着色缺失
（Phase 57 演示靠 _eval 脚本渲染）；cellchat.py 已接受任意
celltype_col（默认 leiden），lineage_branch × sc_cellchat 联用
为纯评估零代码。

## §1 sc_plot umap_obs（产品变更）

- sandbox/sc_tools/plot.py：kind 扩 "umap_obs"，读新参数 obs_cols
  （list、≤6、逐项校验存在于 obs.columns，缺失 → fail
  INVALID_INPUT 列出缺失列）；连续/类别列 sc.pl.umap 自适应；
  violin/umap_gene 路径零变化；png 命名 <col>_umap.png。
- orchestrator/tools/builtin/l3_singlecell.py：sc_plot handler 加
  obs_cols 透传 + kind enum 扩 "umap_obs" + schema 描述更新。
- TDD +2（obs_cols/kind 透传 + 默认值零变化），回归预期 1299。

## §2 测试

- 冒烟：_smoke_sc_pseudotime.py 加场景⑲——DS2 场景⑭后 obs 已含
  slingshot_lineage/lineage_branch，容器调 plot.py
  kind=umap_obs 双列 → 双 png 落盘；非法列名 → INVALID_INPUT。
- 真机：19149 umap_obs 出 slingshot_lineage/lineage_branch
  官方工具版 UMAP（替换 Phase 57 _eval 演示）。

## §3 lineage_branch × sc_cellchat（纯评估零代码）

- 59900 鼠源 celltype_col=lineage_branch 单组跑：三命运支间
  LR 通讯谱（trunk vs 旁支信号差异）；
- 19149 人源同款（二支）交叉对照。

## §4 范围外

cellchat diff 模式（group_col 为样本分组列，与 fate 支正交）、
violin 按 obs 分组扩展、镜像 pip 变更。
