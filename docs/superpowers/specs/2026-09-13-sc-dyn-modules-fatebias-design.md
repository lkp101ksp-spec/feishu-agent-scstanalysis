# 动态基因趋势聚类 + 命运偏向检验（设计，2026-09-13）

增补自 2026-09-13-sc-branch-inference-design.md（用户"按这个
方案执行"批准）。对分支推断已有产物的挖掘升级两件，镜像零
pip 改动：

- **趋势聚类**：从"top N 动态基因列表"升级到"协同表达程序
  模块"——回答轨迹各阶段开启什么生物程序；
- **命运偏向**：sc_cellfreq 补齐 Fisher/Ro-e 短板（Phase 33
  仅有未校正卡方），`celltype_col="palantir_branch"` 即得
  分支归属×分组的命运偏向，通用化服务任意分类列。

## §1 sc_pseudotime 趋势聚类（`_dyn_modules`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `dyn_modules_k` | `0` | 0=跳过（回归零变化）；>0 启用趋势聚类 |
| `modules_enrich` | `""` | 空=跳过；GS key（enrichment.py GS_KEYS 别名）逐模块离线 ORA |

- **流程**：`_dyn_genes` 统计完成后，k>0 且 sig(q<0.05) 基因数
  ≥k → 显著基因**全量**沿 pt 平滑 z-score 矩阵（内存纪律同
  _dyn_genes：整矩阵一次 csc + 列索引字典）→ sklearn
  KMeans(n_clusters=k, n_init=10, random_state=0) → 模块按
  "z>1 高表达细胞的平均 pt"排序重命名 M1..Mk（早→晚程序）；
- **产物**：`<prefix>dyn_modules.csv`（gene, rho, qval,
  module）、`<prefix>dyn_modules.png`（k 条模块均值趋势曲线
  单面板）、可选 `<prefix>dyn_modules_enrich.csv`（module,
  term, overlap, pval, qval；逐模块 gp.enrich 读
  /opt/gene_sets/*.json，enrichment.py 先例；模块基因
  <5 或富集空结果 → note 优雅降级）；
- **emit**：n_modules、module_sizes、dyn_modules_csv/png
  （+dyn_modules_enrich_csv、modules_note）；
- **校验**：sig<k → modules_note 跳过不 fail；modules_enrich
  非法 key → fail INVALID_INPUT（列可用 key）；
- **兼容**：`_dyn_genes` 双引擎共用故 DPT/palantir 两路皆可
  （产物 prefix 区分）；分支内 dyn（_branch_analysis）不做
  模块——范围控制；
- **依赖**：sklearn 随 scanpy 现成，gseapy Phase 已装，零
  pip 改动。

## §2 sc_cellfreq Fisher + Ro/e 增强（零新参数）

- **现状**（Phase 33）：by×celltype 比例 + 每簇卡方（原始
  p 未多重校正）；
- **增强**（输出级，向后兼容）：group 值域恰 2 时——
  1. 每簇 Fisher 精确检验（该簇 vs 其余 × 两组，样本合并
     计数，scipy fisher_exact）→ odds_ratio + fisher_p +
     BH fisher_q；
  2. Ro/e = observed/expected（卡方期望）每簇×每组两列；
- **产物**：现有 cluster_stats csv 加列（odds_ratio,
  fisher_p, fisher_q, roe_g1, roe_g2）+ Ro/e 热图
  cellfreq_roe.png（簇×组，RdBu_r 中心 1 取 log2 着色）；
  emit 加 `fate_bias` 摘要 top5（cluster, odds_ratio,
  fisher_q, higher_in）+ roe_png 键；
- **降级**：group >2 值 → 仅现状卡方 + note（不 fail）；
  簇计数为 0 → Fisher 跳过该簇（or=nan）；
- **用法**：`celltype_col="palantir_branch"` + `group="group"`
  → 59900 G1/G2 命运偏向；同一增强服务任意细胞类型列。

## §3 测试

1. **TDD 单测 +2**：dyn_modules_k/modules_enrich 透传+
   schema；默认 0/"" 零变化断言（cellfreq 零新参数无需
   新单测，现有用例回归兜底）；
2. **冒烟**（镜像重建后）：
   - pseudotime 场景⑫：合成库 dyn50+modules_k=3 → 模块
     csv/png 落盘 + sig 基因全归属 + 恰 3 模块 + 模块 pt
     排序单调（M1 早 Mk 晚）；
   - cellfreq 场景（新/扩）：合成 2 组×3 簇已知偏向 →
     Fisher q 显著簇 = 注入偏向簇 + OR 方向正确 + roe 热图
     落盘；3 组列 → 仅卡方降级 note；
3. **真机**：
   - 19149 palantir root_cluster=2 dyn50+modules_k=6+
     modules_enrich=go_bp → 模块程序解读（classical/basal/
     过渡）+ 富集通路生物学判读 + 耗时打点；
   - 59900 sc_cellfreq celltype_col=palantir_branch
     group=group → G1/G2 命运偏向（三谱系分支哪组富集）；
   - 59900 palantir modules_k=6+modules_enrich=kegg_mouse
     （鼠源库符号 upper 对齐先例）。

## §4 范围外

分支内 dyn 模块聚类；k 自动选择（肘部/silhouette）；
CellRank；milo 口径命运偏向（邻域级，与簇级 Fisher 互补，
后续可选）。
