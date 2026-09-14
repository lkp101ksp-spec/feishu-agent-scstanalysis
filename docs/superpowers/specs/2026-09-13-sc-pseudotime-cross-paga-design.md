# sc_pseudotime 轨迹交叉聚合 + PAGA 簇级分析相设计（2026-09-13，修订版）

## 0. 背景

Phase 55 后 obs 同时具备 palantir 三分支归属（palantir_branch）与
slingshot 12 谱系（slingshot_pt.csv 宽表 + obs slingshot_pseudotime）。
用户批准两件后续并解封四项范围外：①谱系×分支交叉聚合（叙事收紧，
含反向联动+Fisher）；②PAGA 簇级分析相（含 PAGA-initialized DPT）。

技术事实校准：**scanpy 无 paga_paths**（PAGA 独立包函数）；scanpy
`tl.paga` 仅给簇级连接度。PAGA pt 交付物 = 官方 tutorial 配方
PAGA-initialized DPT（PAGA 图推端点簇定根 → DPT）。

## 1. 谱系×分支交叉聚合（共享函数双向触发）

`_branch_lineage_cross(ds_dir, adata, pst_wide, triggered_by)`：

- **触发**（任一侧，产物齐备即跑，内容幂等）：
  - slingshot 侧：引擎跑完且 `obs["palantir_branch"]` 存在 →
    pst_wide 用内存宽表；
  - palantir 侧：branch_top_n>0 归属写回后且
    `ds_dir/slingshot_pt.csv` 存在 → pst_wide 读 csv 宽表
    （数字条码 astype(str) 对齐，场景⑤/⑭同款教训）。
- **计算**：谱系归属=每细胞 lineage1..k argmax（全 NA → unassigned
  行剔除）× palantir_branch 列联；行比例；主导分支（frac≥0.5，
  否则 mixed）；每谱系 × 主导分支 2×2 Fisher（该谱系 vs 其余 ×
  主导分支 vs 其余，样本合并计数）+ BH fisher_q + roe
  （observed/expected，cellfreq.py 已验模式）。
- **产物**：
  - `slingshot_branch_cross.csv`（一行一谱系：n_cells,
    dominant_branch, dominant_frac, fisher_p, fisher_q, roe,
    n_<branch>…, frac_<branch>…）
  - `slingshot_branch_cross.png`（谱系×分支行比例热图 viridis
    0-1 + 数值标注）
- **emit**：`branch_cross_csv/png`、`lineage_branch_map`
  （{lineage: {branch, frac}}）、`n_cross_sig`（q<0.05）、
  `cross_triggered_by`（slingshot/palantir）。

## 2. PAGA 分析相（`paga=1`，任意引擎 pt 后追加）

`_run_paga(adata, pt, clusters, ds_dir, paga_pt)`：

- `sc.tl.paga(adata, groups="leiden")`（neighbors 复用，纯 scanpy
  零新依赖）；边取 `uns["paga"]["connectivities"]` 上三角非零。
- 产物：`paga_graph.csv`（cluster_a, cluster_b, weight 全边）+
  `paga_umap.png`（UMAP 簇质心节点——按簇 pt 均值 viridis 着色，
  weight≥0.1 边、粗细∝weight，灰底细胞点）。
- emit：`n_paga_edges`（weight≥0.1）、`paga_graph_csv`、`paga_png`。

### PAGA pt（`paga_pt=1`，前置 paga=1，否则 INVALID_INPUT）

- 根簇推断：PAGA 图度=1 簇中取本次引擎 pt 均值最小者；无度=1
  簇退化取全局 pt 均值最小簇；显式 root_cluster 直接尊重。
- iroot=根簇内邻居图度中心细胞（`_degree_root` 复用）→
  `sc.tl.diffmap` + `sc.tl.dpt` → `obs["paga_dpt_pseudotime"]`
  统一落盘；paga_umap 节点改按 paga_dpt 簇均值着色。
- emit：`paga_root_cluster`、`paga_root_note`、
  `paga_pt_obs="paga_dpt_pseudotime"`。

## 3. 校验与参数

- 新参数：`paga`（bool 默认 false）、`paga_pt`（bool 默认 false）；
  唯一互斥：paga_pt=1 且 paga=0 → INVALID_INPUT。
- 交叉触发条件不满足时静默跳过（不报错，emit 无交叉键）。
- l3 schema/description 同步（paga 两参数 + 交叉自动触发说明）。

## 4. 测试

- TDD +3（l3）：paga/paga_pt 透传+schema、默认值 false 零变化、
  schema 描述含 paga_pt 前置 paga 说明。
- 冒烟 15→18：
  - ⑭扩展（slingshot 正向）：场景⑩已写 palantir_branch →
    ⑭交叉自动触发；断言交叉双产物、lineage_branch_map 两谱系、
    n_cross_sig≥1、fisher 三列在 csv、cross_triggered_by=slingshot；
  - ⑰ dpt+paga=1：n_paga_edges≥1、双产物落盘；
  - ⑱ palantir+branch50+paga=1+paga_pt=1（反向+组合）：分支四产物
    +交叉产物（triggered_by=palantir）+paga 双产物+
    obs paga_dpt_pseudotime 齐备；另 paga_pt=1&paga=0 拒。
- 真机三跑：19149 palantir+branch50（反向交叉首验人源）→
  19149 slingshot+root2+paga+paga_pt（正向+paga 全相）→
  59900 palantir+branch50+paga+paga_pt（反向全相鼠源）。
- 全量回归预期 **1297**（1294+3）。

## 5. 范围外

- paga 边阈参数化（0.1 写死）、交叉其他检验（卡方/超几何）、
  PAGA 独立包引入、跨数据集交叉。
