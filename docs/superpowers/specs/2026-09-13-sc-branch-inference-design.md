# 分支推断：Palantir 分支归属 + 分支特异基因（设计，2026-09-13）

增补自 2026-09-13-sc-palantir-engine-design.md（方案 A，用户"按方案
A 执行"批准）。回答 Monocle2 BEAM 的核心问题：**哪些基因区分命运
A 与命运 B**——基于 Phase 54 已有的终末态与分支概率，零新依赖。

## §1 参数与开关

| 参数 | 默认 | 说明 |
|---|---|---|
| `branch_top_n` | `0` | 0=跳过（Phase 54 回归零变化）；>0 启用分支推断，取每分支/每对 top N |

- 仅 palantir 引擎有效：`engine="dpt"` 且 `branch_top_n>0` →
  fail INVALID_INPUT；
- `n_terminal < 2`：只做归属（步骤 1），跳过差异（emit note）。

## §2 三步流程（`_branch_analysis`，_run_palantir 内调用）

**步骤 1 分支归属**：细胞按 `argmax(branch_probs)` 归分支；
`max_prob < 0.6` → `"unassigned"`（低置信过渡态不强行站队）。
产物 `palantir_branch_assign.csv`（barcode, leiden, pt, branch,
max_prob）；emit 加 `branch_counts`（每分支细胞数）、
`n_unassigned`。

**步骤 2 分支内动态基因**：每分支（归属细胞 ≥30）在分支内沿 pt
复用 Spearman+BH 逻辑（HVG∩raw 候选池）取 top N——重构提取
`_dyn_stats(Xcsc, col_of, cand, pt, valid)` 供 `_dyn_genes` 与
分支分析共用（零行为变化，_dyn_genes 改用之）。产物
`palantir_branch_dyn.csv`（branch, gene, rho, qval）。

**步骤 3 分支间差异（BEAM-lite 核心）**：两两分支（各归属
≥30 细胞）pt 匹配比较：
1. 两分支细胞各自按 pt 排序分 20 个等量桶；
2. 每候选基因（HVG∩raw）：同序号桶内 A 细胞组 vs B 细胞组
   算中位数差 Δ_b，基因 score=median(|Δ_b|)，方向
   `higher_in`=median(Δ_b) 符号；
3. 显著性：对 20 个桶中位数组做配对 wilcoxon（n=20）→ pval，
   跨基因 BH 校正；
4. 取 qval<0.05 按 score 降序 top N →
   `palantir_branch_de.csv`（pair, gene, score, higher_in,
   pval, qval）。

**对照热图** `palantir_branch_trend.png`：显著基因数最多的那
一对分支，其 top 基因（封顶 20）双面板——A/B 细胞各按 pt 排
序、移动平均平滑 z-score、共享 ±2 色阶；pt 色条置顶。多对时
其余对仅 csv（emit note 说明）。内存纪律沿用 csc 整矩阵。

## §3 错误处理

| 场景 | 行为 |
|---|---|
| engine=dpt 且 branch_top_n>0 | fail INVALID_INPUT |
| n_terminal<2 | 只归属，branch_note 说明 |
| 分支归属 <30 细胞 | 该分支跳过 dyn/DE，note 说明 |
| 所有对均跳过 | emit branch_note，不 fail（归属产物仍在） |

## §4 测试

1. **单测 +2**：branch_top_n 透传+schema；默认 0 零变化断言。
2. **冒烟**：合成双分支库（300 细胞，trunk t∈[0,0.5] 后随机
   分 A/B 两命运延伸 t→1；G_trunk_up 全程升、G_fateA/G_fateB
   仅各自命运后段升，替换式 poisson 注入纪律同 Phase 53）→
   palantir 引擎 branch_top_n=50：①归属率>0.5；②分支 DE top
   含 G_fateA/G_fateB 且 higher_in 方向正确；③三产物落盘；
   ④dpt+branch_top_n>0 拒。
3. **真机 19149**（root_cluster=2，双命运 leiden 5/10）：归属
   分布、分支 DE top 基因生物学判读、耗时打点。

## §5 范围外

Monocle2 DDRTree 移植（重依赖排除）；PAGA 簇级分支（粒度粗）；
分支概率交互可视化。
