# Phase 33：常用分析批量迁移（toolsv1 第三批，A 类四工具）

日期：2026-09-05 ｜ 状态：实施中 ｜ 真机验收：延续用户决策延后，与前两批统一逐个测

## 范围（用户确认：本批四个全选）

| 工具 | 对齐 toolsv1 | 实现 |
|---|---|---|
| sc_de | server_differential_analysis（组间比较） | rank_genes_groups 定向两组对比（group_a vs group_b）→ DEG 表 csv + 火山图 png + 上下调 top 表 |
| sc_subcluster | server_subcluster_standard_analysis | 取指定簇子集从 raw 重跑 HVG/PCA/UMAP/Leiden → **新 dataset_ref**（`{id}_sub{n-m}`），下游工具零改动可链 |
| sc_integrate | server_batch_correction | bbknn（镜像新装 pip 层）批次整合 → **新 dataset_ref**（`{id}_bbknn`），normalize→HVG→scale→PCA→bbknn→UMAP→Leiden |
| sc_cellfreq | server_cell_freq_merged（组成比较） | 按 by 列（样本/分组）× 簇比例表 csv + 堆叠柱状图 + 每簇卡方检验（group 列给出时） |

仍排除：CellChat/Milo/bulk 解卷积（B 类 R 金标准）、SCENIC/虚拟敲除（重依赖）。

## 设计要点

- **sc_de 定向对比**：`rank_genes_groups(groupby=列, groups=[a], reference=b)`；
  列/取值不存在时错误消息列出可用 obs 分类列与已有取值（引导 planner 自纠）；
  上调 = log2fc>0（a 相对 b），同时返回下调 top；火山图 matplotlib 手绘
  （p_adj=0 截断 1e-12）
- **sc_subcluster 新 dataset_ref 方案**：子集从 `adata.raw`（归一化 log 全基因）
  重建矩阵重跑管线，产物落 `WS/{new_id}/processed.h5ad`——markers/富集/打分/
  代谢/拟时序全部不改即可用新 ref（D1，优于同目录 subcluster.h5ad + 改
  load_adata 链的方案：不碰公共代码、天然支持多级亚聚类）
- **sc_integrate 同样出新 dataset_ref**（`{id}_bbknn`）；input 链与 sc_process
  一致（filtered→raw 默认过滤）；`neighbors_within_batch = max(3,
  round(n_neighbors/n_batches))`；method 锁 enum ["bbknn"] 控范围
- **sc_cellfreq 卡方口径**：每簇 2×G 列联（该簇 vs 其余，按 group 汇总样本）
  chi2 + BH 校正由调用侧看原始 p（返回 chi2/p_raw，p 简单不多重校正，注明）
- 四工具均 L1_compute；timeout 1200/1800/1800/600
- 镜像：bio.Dockerfile 在 gseapy 层后追加独立 `RUN pip install bbknn` 层
  （保 gene_sets 缓存层）

## 文件清单

- sandbox/sc_tools/de.py / subcluster.py / integrate.py / cellfreq.py（新建×4）
- sandbox/bio.Dockerfile（+bbknn 层）
- orchestrator/tools/builtin/l3_singlecell.py（+4 注册，共 13 工具）
- tests/unit/test_l3_singlecell.py（+4 组用例）

## 验证

单测（注册/转发/错误透传/schema）+ 本机四脚本冒烟（同 Phase 32 模式，
WS_ROOT/GENE_SET_DIR monkeypatch；de/cellfreq 用带 condition/sample 列的
假 processed）+ docker 增量重建（bbknn 层）+ 断网容器 import + 全量回归。
容器真跑与真机验收：延后到统一测试轮。

## 验证记录（实施完成）

- 单测 27 passed（+6）、ruff 新文件全绿、全量回归 **1025 passed**；
- 本机冒烟四脚本全过，生物学自洽：GZMB↑/MS4A1↓（de）、簇 2 卡方
  p=7.6e-12（cellfreq）、subcluster 新 ref 回读带 raw+leiden、
  integrate 四批 nwb=4；
- **D2 修正**：容器装 bbknn 首次失败（annoy 无 manylinux 轮需 g++ 源码
  编译，slim 无编译器；宿主 Windows 有预编译轮掩盖了该问题）——改为
  同层 `apt install g++ → pip bbknn → apt purge g++`；
- 镜像 edcdbc2e：断网容器 import bbknn(1.6.0) + 四脚本 ok。
