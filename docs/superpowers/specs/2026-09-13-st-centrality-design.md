# st_stats 扩展第四分析 centrality 设计（Phase 46）

日期：2026-09-13
状态：已获用户批准（并入 st_stats 合集方案，否决独立 st_centrality 工具）

## 1. 背景与目标

Phase 45 交付 st_stats 三分析（autocorr / cooccurrence / nhood_enrichment）。
2026-09-13 用户选定 squidpy `gr.*` 层三件套补齐方向（autocorr +
cooccurrence + centrality 合并一轮 Phase）——前两件 Phase 45 已存在，
真缺口只剩 **centrality**：图中心性（degree / clustering / closeness）
+ 簇间互作矩阵，回答"哪种细胞类型/空间域居于组织拓扑枢纽"，补
niche（组成聚类）与 nhood_enrichment（两两富集）之间的全局拓扑视角。

squidpy 1.8.3（st 镜像现成）原生两函数零新依赖：
`gr.centrality_scores(cluster_key)` 与 `gr.interaction_matrix(cluster_key)`。
并入 st_stats 第四 analysis（ANALYSES 枚举扩展），与 st_domains method
参数先例、Phase 45 合集决策一致——否决独立 st_centrality 工具（工具面
膨胀、发现性收益抵不过口径分裂）。

## 2. 输入参数（stdin JSON / ToolSpec schema）

复用 st_stats 现有参数面，**仅 analysis 枚举扩展**，无新参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| analysis | 必填 | 新增第四个合法值 `centrality`（原三值不变） |
| cluster_key | 回退链 | centrality 使用：`spatial_domain`→`banksy_domain`→`leiden`→`clusters`；全缺失报错列可用 obs 列（复用 `_resolve_cluster_key`） |
| coord_type / n_neighs | `grid` / 6 | 邻域图兜底参数（复用 `_build_neighbors`，processed.h5ad 已建图时零开销） |

## 3. 产物

落 `stats_centrality/` 子目录（与其他分析 `stats_{analysis}/` 口径一致）；
图片走 `pngs` 聚合键 + csv 走 `.csv` 键——IM 发图与 D 报告汇编零改动。

- `centrality_scores.csv`：n 簇 ×（degree_centrality,
  clustering_coefficient, closeness_centrality）三列（列名以容器探针
  核实为准，钉入 stats.py 头注释）
- `interaction_matrix.csv`：n×n 簇间互作计数矩阵（行列=簇名）
- `centrality_degree.png`：`pl.centrality_scores` degree 视图
- `interaction_matrix.png`：自绘互作矩阵热图（matplotlib imshow，
  与 nhood 热图风格一致）
- emit 数字：`n_clusters`、`top_hub`（degree 最高簇）、
  `top_hub_degree`、`top_closeness`（closeness 最高簇）

processed.h5ad 只读不改写（沿用 Phase 45 决策）。

## 4. 注册与错误处理

- [l3_spatial.py](../../../orchestrator/tools/builtin/l3_spatial.py)
  st_stats handler 无签名改动（analysis 已透传）；ToolSpec schema 的
  analysis enum 加 `centrality`，描述补"图中心性+簇间互作矩阵：
  识别组织枢纽簇"。timeout 复用 1800s（centrality 为秒级图算法，
  排列检验余量足够）。
- SECTION_TITLES 无需改动（st_stats 已注册，Phase 45 覆盖测试
  已拦截口径）。
- 错误码：`INVALID_INPUT`（analysis 非法，既有分支自动覆盖新枚举）、
  `ST_CLUSTER_KEY_MISSING`（回退链全灭，复用）、`SCRIPT_ERROR` 兜底。
- stats.py 头注释按惯例钉容器探针核实结论（uns 键名 / DataFrame 列名 /
  interactions 矩阵类型）——探针为实施第一步（Phase 45 同款流程）。

## 5. 测试

1. **注册单测**：payload 透传 `analysis="centrality"` + cluster_key；
   schema enum 含四值断言。
2. **集成测试**（tests/integration/test_bio_st_stats.py 追加，合成
   网格数据）：造两个空间分离簇（左半大簇 + 右半两小块）→
   断言 `top_hub`=大片簇（邻接面最广）、interaction 矩阵对角线计数
   显著高于非对角（同簇自互作主导）。
3. **真机验收**：真实 OSCC processed.h5ad 跑 centrality——预期
   肿瘤/SCC 域 degree 最高（致密巢区邻接广），与病理注释交叉对照。
4. 镜像重建后断网容器迷你跑验证（stats.py 改动必须重建 st 镜像，
   镜像纪律）。

## 6. 非目标（YAGNI）

- 不做独立 st_centrality 工具（并入合集，见 §1 决策）；
- 不做 Ripley's L / 其他 gr.* 函数（后续按需追加 analysis 枚举）；
- 不写回 processed.h5ad；不改 IM/报告链路（pngs 键口径已泛化）；
- 不做按 dataset 多切片批量（st_stats 现口径为单 processed.h5ad，
  多切片是独立议题）。
