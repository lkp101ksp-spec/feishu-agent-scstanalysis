# st_vicinity 肿瘤邻域分层设计（Phase 48）

日期：2026-09-11
状态：用户已批准（"好的，按这个方案来"；种子方案甲=仅恶性区域种子）

## §1 定位与形态

`st_vicinity`：st_* 第 12 个工具。以 st_cnv 判定的恶性 spot 为种子，
沿空间邻居图 BFS 向外分层（tool-spatial-niche vicinity 方法论），刻画
"肿瘤核心→侵袭前沿→远端"邻域梯度。

- L3 注册：`orchestrator/tools/builtin/l3_spatial.py` handler + ToolSpec
  （`risk_level="L1_compute"`，`timeout_sec=600`——纯图遍历）
- 容器脚本：`sandbox/st_tools/vicinity.py`（**镜像零改动**——
  scipy.sparse 图遍历 + squidpy 均已烘焙，COPY 层秒级重建）

## §2 数据流

```
processed.h5ad
  ├─ obs["is_malignant"]==True → 种子（st_cnv 写回）
  ├─ obsp["spatial_connectivities"] → 邻居图（st_process 已建；
  │    缺失则 _build_neighbors 补建，st_stats 同款：
  │    grid n_neighs=6 / generic delaunay）
  └─ obsm["spatial"]（ensure_spatial 校验）
deconv.h5ad（可选）→ 层×细胞型组成统计
        ↓ BFS
obs["vicinity"] 写回（tumor / L1..L{max} / distal）
```

- **种子**：`obs["is_malignant"]==True`；列缺失或零恶性 → fail
  `ST_VICINITY_NO_SEED` 引导先跑 st_cnv（不静默降级）
- **BFS 分层**：种子标 `tumor`；逐层外扩 `L1..L{max_layers}`；
  未达 spot 标 `distal`；稀疏图用 `scipy.sparse.csgraph` 或手工
   frontier 扩张（indptr 索引，免稠密化）
- 写回按 obs_names reindex 对齐（st_plot 可着色、st_stats 可作
  cluster_key）

## §3 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| max_layers | int | 5 | BFS 最大层数；1..10，越界 INVALID_INPUT |
| coord_type | str | `grid` | 补建邻居图坐标类型（grid/generic），白名单校验 |

## §4 产物（落 `/ws/{ds}/vicinity/`）

- `vicinity_spatial.png`：分层着色 spatial_scatter（固定调色：tumor
  红 / L1..Ln 橙黄渐变 / distal 浅灰；`_scatter_img_kwargs` 占位壳）
- `vicinity_layer_sizes.csv`：层标签 × spot 数
- **层×细胞型组成（条件产物）**：deconv.h5ad 存在时输出
  `vicinity_composition.csv` + `vicinity_composition_heatmap.png`
  （层 × 细胞型均值组成，行按 tumor→Ln→distal 排序——免疫/基质随
  距离梯度变化是 vicinity 核心解读图）；deconv.h5ad 缺失则**跳过
  不报错**（分层本身不依赖反卷积），note 与 has_composition 注明
- emit 键：`pngs` 聚合键 + 单名键 + layer_sizes / n_tumor /
  n_reached / max_layers / has_composition
- `section_digest.py` SECTION_TITLES 加 `"st_vicinity": "肿瘤邻域分层"`

## §5 错误码

| 码 | 场景 |
|---|---|
| ST_VICINITY_NO_SEED | obs 缺 is_malignant 列或零恶性 spot（引导先跑 st_cnv） |
| INVALID_INPUT | max_layers 越界 / coord_type 非白名单 |
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（ensure_spatial） |
| SCRIPT_ERROR | 兜底（run() 包装） |

## §6 测试

- **TDD 注册 5 用例**（`tests/unit/test_l3_st_vicinity.py`，同模板）：
  registered（L1_compute/timeout 600/max_layers 默认 5）/
  forwards_params / defaults / ok_stripped / error_passthrough；
  `test_l3_spatial.py` 工具数断言 11→12（两处）
- **断网容器冒烟**（`scripts/_smoke_st_vicinity.py`）：合成 12×12
  网格左半 is_malignant=True + 配套 deconv.h5ad 组成（T cells 随
  距离种子递减、Tumor 集中种子）→ 断言 tumor 恰为左半（72）、
  max_layers=2 时 L1/L2 紧邻种子右缘且远端标 distal、组成产物出现
  且 Tumor 组成 tumor 层显著高于 distal、ST_VICINITY_NO_SEED
  （无列数据集）与 max_layers=99 越界两错误路径；obs 写回容器回读
- 全量回归（`pytest -q -m "not pg"` 门禁口径，1255 基线 +5 = 1260）
  + pre-push 四道门 + CI 绿

## 非目标

- 任意 obs 列泛化种子（用户拍板甲方案：仅恶性区域种子）
- 层内差异表达/通路梯度分析（下游 sc_enrichment 等可接力）
- 多切片联合分层 / 侵袭方向向量场（方法学研究范畴）
