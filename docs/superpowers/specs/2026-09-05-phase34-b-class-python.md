# Phase 34 设计：B 类分析 Python 等价物三件套（cellchat / milo / deconv）

日期：2026-09-05
状态：设计已获用户批准（对话内 "OK"）
前置：Phase 31-33（13 个 sc_* 工具，commit 343509c）

## 1. 背景与决策

toolsv1 的 B 类模块（CellChat / MiloR / bulk 解卷积 / scTenifoldKnk）盘点结论：

- 全部为 **R/Shiny** 实现，I/O 以 Seurat RDS 为中心，合计约 1.6 万行；
- bulk 解卷积与虚拟敲除的核心 `utils/` 文件**缺失于 toolsv1 快照**（deconvolution_utils.R + 5 个敲除工具文件），忠实迁移已不可能，无论如何都要重写；
- 无强联网数据库依赖（CellChatDB、org.*.eg.db 随包分发）。

技术路线（用户决策）：**Python 等价物**，复用现有 `bio:cpu-latest` 镜像。

- 否掉"独立 R 镜像"：Bioconductor 构建慢、镜像 3-5GB、Seurat v5 与 h5ad 桥接有已知坑；
- 否掉"rpy2 混合"：R/Python 版本矩阵复杂，镜像仍显著变大。

## 2. 范围

| 新工具 | 替代 toolsv1 | 实现方式 |
|---|---|---|
| `sc_cellchat` 细胞通讯 | CellChat（约 1.1 万行 R） | liana（内置 cellchat 方法 + 随包 consensus 资源，离线可用） |
| `sc_milo` 差异丰度 | MiloR（约 2.2 千行 R） | Python 复刻：KNN 邻域采样 → 逐样本计数 → statsmodels NB-GLM → BH |
| `sc_deconv` bulk 解卷积 | BayesPrism/MuSiC 等 | wNNLS（MuSiC 式加权）+ 可选 nusvr（CIBERSORT 式，sklearn NuSVR） |

**不做**（YAGNI）：多组 CellChat 比较（toolsv1 part1-5 套件）、BayesPrism 完整贝叶斯模型、CIBERSORTx 在线 API、scTenifoldKnk（维持重依赖暂缓）、st_* 空间线。

## 3. 工具设计

### 3.1 sc_cellchat（细胞通讯）

- 输入：`dataset_ref`（processed.h5ad）、`celltype_col`（默认 `leiden`）、`min_cells`（默认 10，细胞类型低于此数剔除）、`top_n`（默认 30）、`expr_prop`（默认 0.1）、`species`（human/mouse，默认 human）
- 流程：liana `method='cellchat'`（内置 CellChatDB 系资源，随包分发离线可用）→ 汇总 LR 对
- 产出：`cellchat_lr.csv`（全量显著 LR 对：ligand/receptor/source/target/prop/lrscore/pval 等）、`cellchat_dotplot.png`（top LR × 细胞类型对）、`cellchat_heatmap.png`（细胞类型间互作计数热图）
- 错误自纠：celltype_col 不存在时列出可用 obs 分类列（复用 de.py 的 `_cat_cols()` 模式）
- timeout：1800s

### 3.2 sc_milo（差异丰度）

- 输入：`dataset_ref`、`sample_col`、`group_col`、`group_a`、`group_b`、`k`（默认 0，自动=clip(round(0.1×最小样本细胞数),10,50)）、`top_n`（默认 20）
- 流程：
  1. 复用 processed.h5ad 的 KNN connectivities（无则用 PCA 重建，n_neighbors=15）
  2. 邻域采样：随机种子细胞 + refined 式去重（贪心，邻域重叠度阈值 0.8 跳过后续种子）
  3. 逐样本×邻域计数矩阵 → NB-GLM（statsmodels，`group ~ 1`，offset=log(样本总细胞数)）
  4. BH 校正（注明：非 miloR 的 SpatialFDR 加权校正，口径偏保守/不同，文档写明）
- 产出：`milo_da.csv`（nhood 结果：logFC/PValue/FDR/主要细胞类型构成）、`milo_umap.png`（UMAP 按 nhood logFC 着色，FDR<0.1 邻域高亮）
- 校验：sample_col/group_col 存在性；每组样本数≥2 否则报错（GLM 无复制无法估计）
- timeout：1800s

### 3.3 sc_deconv（bulk 解卷积）

- 输入：`dataset_ref`（sc 参考，processed.h5ad）、`celltype_col`、`bulk_file`（data roots 下 csv/tsv，行=基因列=样本）、`method`（`wnnls` 默认 / `nusvr`）、`top_n`（默认 200，signature 基因数）
- 流程：
  1. sc 参考：raw counts → CPM 标准化 → 按细胞类型取均值得 signature 矩阵 S（基因×类型）
  2. signature 基因选择：按类型间方差取 top_n 基因（wnnls）；nusvr 再按 CIBERSORT 式逐步特征（v1 简化为同 top_n）
  3. bulk 与 S 取基因交集（<50 报错提示基因 ID 不一致）
  4. `wnnls`：scipy.optimize.nnls，权重 = 1/sqrt(参考内该基因跨细胞方差 + eps)；`nusvr`：sklearn NuSVR(nu=0.5, linear)
  5. 负系数截 0，列归一（和=1）
- 产出：`deconv_proportions.csv`（样本×类型比例）、`deconv_signature.csv`（signature 矩阵，便于复现）、`deconv_barplot.png`（堆叠柱）、`deconv_heatmap.png`
- bulk_file 走 data roots 白名单（复用 load.py 的路径校验），不新开口子
- timeout：1200s

## 4. 依赖与镜像

- 新增 pip：仅 `liana`（statsmodels/sklearn 已由 scanpy 传递依赖带入，Dockerfile 注释核实）
- Dockerfile 独立层（保上方缓存层）；构建后**断网容器 import + 三脚本真跑冒烟**（liana 资源库是否随包分发是主要风险点，构建期验证，若联网拉资源则需构建期预取到 /opt/gene_sets/ 同类目录）
- 沿用 Phase 33 教训：带原生编译依赖的包在 Dockerfile 层面预检（liana 依赖 numba/plotnine 等均有 manylinux 轮，预期无 g++ 需求，构建时确认）

## 5. 注册与测试

- [l3_singlecell.py] +3 ToolSpec（13→16 工具），handler 沿用 `runner.run + _err + pop ok` 模式
- 单测 6-8 用例：注册断言×3 + 转发×3 + 错误自纠（celltype_col 错误列可用值）
- 本机冒烟（双 WS_ROOT monkeypatch 模式）：假数据生物学自洽——
  - cellchat：构造强自互作簇，验证其 LR 对排名靠前
  - milo：构造某簇在 group_b 两样本中占比翻倍，验证 logFC>0 且 FDR 显著
  - deconv：人工混合已知比例 bulk（3 类型 70/20/10），验证 wnnls 还原误差 <10%
- 全量回归（`--basetemp=.pytest_tmp`，日志落 .pytest_last.log 取统计行）
- 四文档同步：ROADMAP / 平台功能说明书（16 工具表）/ 使用说明书（触发话术）/ 测试总结
- 容器真跑与真机验收并入统一测试轮（沿用用户既定决策）

## 6. 文件清单

| 文件 | 动作 |
|---|---|
| sandbox/sc_tools/cellchat.py | 新建 |
| sandbox/sc_tools/milo.py | 新建 |
| sandbox/sc_tools/deconv.py | 新建 |
| sandbox/bio.Dockerfile | 加 liana 层 |
| orchestrator/tools/builtin/l3_singlecell.py | +3 ToolSpec |
| tests/unit/test_l3_singlecell.py | +6-8 用例 |
| docs/ROADMAP.md / 双说明书 / 测试总结 | 同步 |
