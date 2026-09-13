# sc_cellchat 增强设计：rank_aggregate 聚合 + 两组差异通讯（Phase 47）

日期：2026-09-13
状态：已获用户批准（合并一轮两阶段：先聚合升级后差异通讯）

## 1. 背景与目标

sc_cellchat 为 Phase 34 已交付工具（liana cellchat 单方法 + 内置
consensus 资源库，bio 镜像断网可用），两套真实图谱（19149 OSCC /
59900 图谱）均已跑过单组推断。2026-09-13 用户选定两项增强合并
一轮 Phase：

- **阶段 1**：`method` 参数——可选 liana `rank_aggregate`（招牌五方法
  共识：cellphonedb/cellchat/natmi/sca/logfc 聚合排序，假阳性低于
  单方法）；默认 `cellchat` 保持现状不变。
- **阶段 2**：`group_col` 参数——两组差异通讯（cellchat.py 头注释
  明示的未做项）；两图谱 group 值域均恰为两组（A/B、G1/G2，
  已探查），场景天然成立。

零镜像变更（liana 与资源库已在 bio 镜像）；零新依赖。

## 2. 输入参数（stdin JSON / ToolSpec schema 增量）

| 参数 | 默认 | 说明 |
|---|---|---|
| method | `cellchat` | `cellchat`（现状）\| `rank_aggregate`（五方法共识） |
| group_col | `""` | 留空=单组（现状）；非空时按该 obs 列拆两组做差异通讯，**值域必须恰 2**（否则 `INVALID_INPUT` 引导子集；多组两两不做，YAGNI） |

其余参数（celltype_col/species/expr_prop/min_cells/top_n/
max_cells_per_group）不变，两阶段均生效（差异模式每组独立抽样）。

## 3. 产物

**阶段 1（method=rank_aggregate）**：

- 显著口径：cellchat 路=pval<0.05（现状）；rank_aggregate 路=
  magnitude_rank / specificity_rank 口径（**容器探针核实确切列名
  与阈值惯例后钉入 cellchat.py 头注释**；dotplot 大小映射随口径
  适配，`_find_col` 防御模式沿用）
- 产物文件与现状同构（cellchat_lr.csv / dotplot / heatmap），
  emit 加 `method` 字段

**阶段 2（group_col 非空）**：

- 每组单组产物落 `cellchat/{group_value}/` 子目录（csv+双图）
- `cellchat_diff/diff_lr.csv`：每 (lr, source→target) × 两组
  score/显著性（outer join）+ `delta_score` + `up_in`（上调方向）
- `cellchat_diff/diff_heatmap.png`：两组显著互作计数差热图
  （红蓝发散：红=G1 上调，蓝=G2 上调）
- emit：`n_sig_g1/n_sig_g2`、`top_delta`（|delta_score| 最强 LR 对
  含方向）、`method`、`group_col`

processed.h5ad 只读不改写（沿用现状）。

## 4. 注册与错误处理

- [l3_singlecell.py](../../../orchestrator/tools/builtin/l3_singlecell.py)
  sc_cellchat handler 加两参数透传；schema properties 加 method
  （enum 二值）与 group_col（描述含恰两组约束）。timeout 沿用
  现值（rank_aggregate 五方法成本约 5×单方法——探针实测 19149
  规模耗时，若超现 timeout 再上调并同步 ToolSpec.timeout_sec
  一致性纪律）。
- 错误码：`INVALID_INPUT`（method 非法 / group_col 值域≠2 /
  group_col 不存在）、既有 celltype_col 缺失自纠（`_cat_cols`
  列可用列）不变、`SCRIPT_ERROR` 兜底。
- cellchat.py 头注释钉容器探针结论（rank_aggregate 输出列名 /
  显著阈值口径 / 19149 规模耗时实测）。

## 5. 测试

1. **注册单测**（test_l3_singlecell.py 既有 sc_cellchat 用例扩展）：
   method/group_col 透传不变形；schema method enum 二值断言；
   默认值 payload 断言（method=cellchat、group_col=""）。
2. **宿主冒烟**（新建 scripts/_smoke_sc_cellchat.py——该工具
   Phase 34 交付时无冒烟脚本，本次补齐）：合成两细胞群 × 两组——A 组群1高表达配体、群2高表达
   受体（造强 LR 对，选 consensus 库内经典单链对如 CCL5|CCR5），
   B 组不造 → 断言：①method=rank_aggregate 单组（A 组）检出该对
   入 top；②group_col 差异模式该对 `up_in`=A 组；③method 非法 /
   group_col 三值 → INVALID_INPUT。
3. **真机验收**（19149 OSCC，celltypist_label 14 类）：
   ①method=rank_aggregate 单组跑通，top LR 与既有 cellchat 单方法
   结果交叉对照（期望共识对重叠过半）；②group_col=group 差异模式
   跑通，diff_lr.csv + diff_heatmap.png 落盘，top_delta 生物学
   合理（OSCC A/B 组免疫-上皮互作差异）。59900 图谱选做（groupby
   键为 leiden/ann_* 分层，非本 Phase 验收硬指标）。
4. 无需镜像重建（liana 已在镜像）；若探针发现 liana 版本行为
   异常再评估。

## 6. 非目标（YAGNI）

- 不做多组（>2）两两差异（group_col 值域恰 2 硬约束）；
- 不做 liana 其余单方法逐个暴露（cellphonedb/natmi/sca/logfc
  单跑——rank_aggregate 已涵盖其共识，单方法仅保留 cellchat 现状）；
- 不做空间通讯改动（st_commot 独立链路不动）；
- 不写回 processed.h5ad；不改 IM/报告链路（png 键口径沿用现状
  dotplot_png/heatmap_png + diff_png 新增键按宿主收图口径核对）。
