# st_niche 空间生态位重构设计（Phase 47）

日期：2026-09-11
状态：用户已批准（"好的，按这个方案来"）

## §1 定位与形态

`st_niche`：st_* 第 11 个工具。基于细胞类型组成的空间生态位（niche）
层次聚类重构——每个 spot 的"细胞邻域身份"。

- L3 注册：`orchestrator/tools/builtin/l3_spatial.py` 新增 handler +
  ToolSpec（`risk_level="L1_compute"`，`timeout_sec=600`——纯聚类计算轻）
- 容器脚本：`sandbox/st_tools/niche.py`（st 镜像，**零镜像改动**——
  只需 scipy/pandas/matplotlib/squidpy，均已烘焙）

## §2 数据流

```
deconv.h5ad ── obsm["q05_cell_abundance_w_sf"] ──┐
   （cell2location 后验丰度，列名去前缀）          ├─ 行归一化组成矩阵
processed.h5ad ── obsm["spatial"]（ensure_spatial）┘   ↓ ward 层次聚类
                obs_names 交集对齐 ← obs["niche"] 写回
```

- **组成矩阵**：读 `/ws/{ds}/deconv.h5ad` 的
  `obsm["q05_cell_abundance_w_sf"]`（deconvolve.py L175 实测结构，DataFrame
  列名带 `q05cell_abundance_w_sf_` 前缀，strip 后为细胞型名）；
  **行归一化为组成比例**（tool-rctd 权重行归一化口径——绝对丰度受
  spot 细胞量干扰，niche 是组成概念）
- **坐标/写回**：processed.h5ad（ensure_spatial 校验），niche 标签按
  obs_names 交集写回 `obs["niche"]`（st_plot 可着色；st_cnv/st_stats
  写回同例）
- deconv.h5ad 缺失 → fail `ST_NICHE_NO_DECONV` 引导先跑 st_deconvolve
  （st_cnv deconv 分支同先例）

## §3 聚类方法（tool-spatial-niche 方法论）

`scipy.cluster.hierarchy.linkage(X, method="ward")` +
`fcluster(t=k, criterion="maxclust")`，k 参数**默认 12**（PDAC 工作流
口径）。每 niche 报 dominant 细胞型（该 niche 均值组成 argmax）。
跨切片 archetype 对齐（匈牙利指派）属多切片范畴——非目标。

## §4 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| k | int | 12 | niche 数；2 ≤ k < n_spots，越界 INVALID_INPUT |

## §5 产物（落 `/ws/{ds}/niche/`）

- `niche_spatial.png`：niche 分类着色 spatial_scatter
  （`_scatter_img_kwargs` 占位壳模式，return_ax=True）
- `niche_composition_heatmap.png`：niche × 细胞型均值组成热图
  （行=niche 按 dominant 型排序，annot 数值）
- `niche_composition.csv`：同上矩阵数据（index=niche 标签）
- emit 键：`pngs` 聚合键（IM/D 报告纪律）+ 单名键 + n_niches /
  niche_sizes / dominant_by_niche / cell_types
- `section_digest.py` SECTION_TITLES 加 `"st_niche": "空间生态位重构"`

## §6 错误码

| 码 | 场景 |
|---|---|
| ST_NICHE_NO_DECONV | deconv.h5ad 不存在（引导先跑 st_deconvolve） |
| INVALID_INPUT | k 越界（<2 或 ≥n_spots）/ deconv 与 processed 交集 <100 |
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（ensure_spatial） |
| SCRIPT_ERROR | 兜底（run() 包装） |

## §7 测试

- **TDD 注册 5 用例**（`tests/unit/test_l3_st_niche.py`，st_stats/st_cnv
  模板）：registered（L1_compute/timeout 600/k 默认 12）/
  forwards_params / defaults / ok_stripped / error_passthrough；
  `test_l3_spatial.py` 工具数断言 10→11（两处）
- **断网容器冒烟**（`scripts/_smoke_st_niche.py`）：合成 deconv.h5ad
  三区组成（左纯 T cells、右纯 Tumor、中间 50/50 混合带，12×12 网格）
  + processed.h5ad 坐标 → k=3 断言混合带独立成 niche 且面积≈中带、
  dominant 型正确、产物 3 件齐全、pngs 键、ST_NICHE_NO_DECONV 与
  k 越界两错误路径；obs 写回容器回读
- 全量回归（`pytest -q -m "not pg"` 门禁口径，1250 基线 +5 = 1255）
  + pre-push 四道门 + CI 绿

## 非目标

- 跨切片 niche 对齐（archetype 正则打分 + 匈牙利一一指派——多切片
  范畴，真实多切片数据到位后再议）
- vicinity BFS 分层（留 st_vicinity 独立工具）
- Banksy 式表达+空间联合特征 niche（st_domains 已覆盖表达侧域识别）
- obs 注释列 one-hot 组成回退（组成矩阵是 niche 意义所在，YAGNI）
