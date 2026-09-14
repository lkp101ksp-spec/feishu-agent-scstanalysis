# Phase 57 设计：交叉产物 obs 写回——打通 sc_plot/sc_cellfreq 消费链

日期：2026-09-14｜状态：已批准（用户"好的，按这个方案来"）

## §0 背景

Phase 56 交付谱系×命运支交叉聚合（csv/png/emit），但交叉结果不落 obs：
sc_plot 无法按谱系/命运支着色、sc_cellfreq 无法以谱系为 celltype_col 做命运偏向。
本 Phase 把两列物化写回 obs，打通下游消费链。零新参数、零 schema/l3 变更。

## §1 变更点（sandbox/sc_tools/pseudotime.py）

1. **slingshot 支写 `obs["slingshot_lineage"]`**：argmax 谱系归属
   （`lineage1..k`；宽表全 NA 的细胞 → `unassigned`），pd.Categorical。
   与既有 `slingshot_pseudotime` 写回同处，走 main 统一 write_h5ad。
2. **`_branch_lineage_cross` 内写 `obs["lineage_branch"]`**（双向触发点共用）：
   细胞级命运支标签 = 其谱系的主导支（`lineage_branch_map` 物化）；
   mixed 谱系的细胞标 `"mixed"`；unassigned 细胞标 `"unassigned"`；
   palantir 支触发时 palantir 支已统一落盘，slingshot 支触发时同。
3. emit 不变（产物 csv/png/map 已齐），无互斥校验变化。

## §2 测试

- l3 单测零新增（无参数面变化）；全量回归基线维持 **1297 passed**。
- 冒烟 18 场景不加新场景、断言扩写：
  slingshot 场景（⑭）重读 h5ad 断言 `slingshot_lineage` 列存在、
  取值 ∈ {lineage\d+, unassigned}；交叉场景（⑭b/⑱）断言
  `lineage_branch` 列存在且与 `lineage_branch_map` 映射一致。
- 真机演示：
  19149 slingshot+root2 重跑（触发交叉写回）→ sc_plot
  `color=slingshot_lineage` / `color=lineage_branch` 出 UMAP 目检；
  59900 palantir+root31 锚定重跑（写回）→ sc_cellfreq
  `celltype_col=lineage_branch` × group G1/G2 命运偏向。

## §3 范围外

谱系级 dyn 基因、lineage_branch × cellchat 联用、新工具、镜像 pip 变更。
