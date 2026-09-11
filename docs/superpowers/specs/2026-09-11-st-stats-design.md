# st_stats 空间统计分析设计（Phase 45）

日期：2026-09-11
状态：已获用户批准（甲方案：单工具三分析）

## 1. 背景与目标

Phase 21 落地 st_* 8 工具（load/qc/process/markers/plot/domains/commot/deconvolve），
缺空间统计学经典层。squidpy 1.8.3（st 镜像现成）`gr.*` 四函数直接可用：
`spatial_neighbors / spatial_autocorr / co_occurrence / nhood_enrichment`。

本 Phase 新增第 9 个 st 工具 **st_stats**，单工具承载三分析（autocorr /
cooccurrence / nhood_enrichment），空间邻域图内部自动构建。工具面 8→9
收敛（否决三独立工具膨胀方案与一次全跑方案），与 st_domains method
参数先例一致。

## 2. 输入参数（stdin JSON / ToolSpec schema）

| 参数 | 默认 | 说明 |
|---|---|---|
| dataset_ref | 必填 | 数据集 ref（12hex） |
| analysis | 必填 | `autocorr` \| `cooccurrence` \| `nhood_enrichment` |
| mode | `moran` | 仅 autocorr：`moran`（Moran's I）\| `geary`（Geary's C） |
| genes | 空=高变基因前 50 | 仅 autocorr：显式基因列表覆盖 |
| cluster_key | 回退链 | 后两个分析：`spatial_domain`→`banksy_domain`→`leiden`→`clusters`；全缺失报错列出可用 obs 列（sc_de 式自纠引导） |
| n_perms | 1000 | 仅 nhood_enrichment：排列检验次数 |
| coord_type | `grid` | 邻域图类型；非网格数据传 `generic` 走 Delaunay |
| n_neighs | 6 | grid 模式邻居数（Visium 六邻居） |

## 3. 产物

落 ds 目录；图片走 `pngs` 聚合键 + csv 走 `.csv` 键——IM 发图与 D 报告
汇编零改动（52f3b84 教训：emit 键必须在宿主收图口径内）。

- **autocorr**：`autocorr_{mode}.csv`（逐基因统计量 + pval_sim + qval）
  + `autocorr_top.png`（top4 基因空间分布）；数字：n_genes_tested、
  top_gene、top_stat
- **cooccurrence**：`cooccurrence.csv`（簇对 × 距离概率长表）
  + `cooccurrence.png`（共现曲线）；数字：n_clusters
- **nhood_enrichment**：`nhood_zscore.csv` + `nhood_count.csv` 双矩阵
  + `nhood_enrichment.png`（热图）；数字：top_pair、top_zscore

processed.h5ad 只读不改写（邻域图内存构建，YAGNI）。

## 4. 注册与错误处理

- [l3_spatial.py](../../../orchestrator/tools/builtin/l3_spatial.py) 第 9 工具
  `st_stats`：`runner.run("stats", {...}, image=st_image,
  script_dir=_ST_SCRIPT_DIR)`；timeout 1800s（排列检验余量），
  handler 与 ToolSpec.timeout_sec 一致纪律（l3_singlecell 头注释同款）。
- 错误码：`INVALID_INPUT`（analysis/mode 非法）、
  `ST_CLUSTER_KEY_MISSING`（回退链全灭 + 可用列提示）、
  `SCRIPT_ERROR` 兜底（common.run 统一包装）。

## 5. 测试

1. **注册单测**：参数透传（analysis/mode/genes/cluster_key/n_perms）、
   timeout 一致、错误分支。
2. **宿主冒烟**（合成网格数据）：
   - 注入空间梯度基因 → Moran's I 显著高于随机基因；
   - 相邻簇 nhood zscore 显著为正、远离簇对不高；
   - 共现曲线形状正确（近端概率 > 远端）。
3. **SECTION_TITLES** 补 `st_stats=空间统计分析`（7f0cf2e 覆盖测试
   自动拦截漏配）。
4. 镜像重建后断网容器 import + 迷你跑验证。

## 6. 非目标（YAGNI）

- 不写回 processed.h5ad（邻域图每次内存重建，秒级开销）；
- 不做 Ripley's L / 其他 gr.* 函数（后续按需追加 analysis 枚举）；
- 不改 IM/报告链路（pngs 键口径已泛化）。
