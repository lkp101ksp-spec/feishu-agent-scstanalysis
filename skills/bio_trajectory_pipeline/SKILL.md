---
name: bio_trajectory_pipeline
description: 单细胞轨迹分析一键编排（trajectory_full 全景+谱系着色+命运偏向+支间通讯，含 root_cluster 锚定纪律与解读陷阱）
---
对 processed h5ad（须含 X_pca/X_umap/leiden）执行轨迹全链编排，复用 bio 镜像
内 sc_tools（与 L3 同一份代码），按 Phase 53-59 最佳实践串五步：

1. `trajectory_full`（sc_pseudotime）：一次产出 palantir 分支推断（命运 DE）+
   slingshot 谱系 + 谱系×命运支交叉 + PAGA；dyn_modules_k=6、modules_enrich=
   go_bp（模块富集库）、paga、paga_pt 均开。
2. `list_cols`（sc_meta 只读）：发现分组列（group_cols）与已有轨迹列。
3. `umap_obs`（sc_plot）：lineage_branch / slingshot_lineage UMAP 着色。
4. 命运偏向（sc_cellfreq）：celltype_col=lineage_branch；存在 group 列时给出
   Fisher 偏向。
5. steps=full 时支间通讯（sc_cellchat）：celltype_col=lineage_branch，
   species 按基因组选库。

## root_cluster 锚定纪律（最高优先级）

- **必须显式提供 root_cluster**（祖/干细胞簇）。fallback 根常落谱系尖端 →
  slingshot 单谱系退化（n_lineages=1、n_cross_sig=0）。若用户未给：先按
  marker 推断（高干细胞/祖细胞标记簇），**向用户确认后再执行**，不要猜。
- 跨引擎相关性比较必须同 root_cluster；palantir 终末态以 cell barcode 命名，
  支名前缀即样本前缀时警惕"支≈病人"共线（先查 branch × batch/sample 列联，
  见 5.2 解读陷阱）。

## 参数

- dataset_id（必填）：bio_workspace 下数据集目录名。
- root_cluster（必填）：锚定根簇（leiden 簇号字符串）。
- species：human|mouse（cellchat 库选择），默认 human。
- steps：fast=1-4 步（~3-10min）| full=含 cellchat（人源 ~15min、
  大图谱可达 35min+），默认 fast。
- group_col：可选分组列名；缺省用 list_cols 发现的 group 列。

## 产物

- summary：bio_workspace/<ds>/trajectory_pipeline_summary.json（各步 ok、
  n_lineages、n_cross_sig、fate_bias top、全部 csv/png 路径）。
- 各步明细落在 bio_workspace/<ds>/pseudotime|plot|cellfreq|cellchat/。

## 解读陷阱（向用户汇报时遵守）

- lineage_branch 覆盖率应为 100%（谱系归属兜住 palantir unassigned）；
- lineage × branch 的 frac<0.5 → mixed 正确降级，勿强行归支；
- n_terminal<2 时无 branch_dyn/DE（单分支数据属正常）；
- T 身份基因（TRAC/CD247 等）沿拟时下调=程序退场而非去分化。
