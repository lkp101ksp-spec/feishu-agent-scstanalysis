# st_misty 多视图空间建模设计（Phase 49）

日期：2026-09-11
状态：用户已批准（"好的，按这个方案来"；路线甲=liana Python MISTy）

## §1 定位与形态

`st_misty`：st_* 第 13 个工具。liana MISTy 多视图学习——细胞型组成
为 intra 目标视图，基因表达为 juxta（紧邻）+ para（旁分泌半径）预测
视图，回答"哪些细胞型/基因在空间上互相解释"。

- L3 注册：`orchestrator/tools/builtin/l3_spatial.py` handler + ToolSpec
  （`risk_level="L1_compute"`，`timeout_sec=1800`——RF 逐目标建模较重）
- 容器脚本：`sandbox/st_tools/misty.py`
- **镜像加一层** `pip install liana`（纯 Python/scverse，无 R 运行时）

## §2 API 事实（2026-09-11 官方教程探明）

```python
from liana.method import MistyData, genericMistyData
from liana.method.sp import RandomForestModel, LinearModel
misty = genericMistyData(intra=comps, extra=acts,
                         cutoff=0.05, bandwidth=200, n_neighs=6)
misty(model=RandomForestModel, n_jobs=-1, verbose=True)
# 结果：misty.uns["target_metrics"]（target/intra_R2/multi_R2/gain_R2/
#   各视图贡献列）与 misty.uns["interactions"]（target/predictor/view/
#   importances）
comps = li.ut.obsm_to_adata(adata, "compositions")  # obsm → AnnData
```

intra AnnData 需带 obsm["spatial"]；genericMistyData 自建 juxta
（n_neighs 邻居加权）与 para（bandwidth 半径加权）视图。

## §3 数据流

```
deconv.h5ad obsm["q05_cell_abundance_w_sf"] → intra（细胞型组成，目标）
processed.h5ad top HVG 表达（已 log1p）     → extra（基因表达，预测子）
processed.h5ad obsm["spatial"]              → 坐标（ensure_spatial）
        ↓ genericMistyData → juxta + para 视图
        ↓ RandomForestModel（n_jobs=2 内存纪律）
uns["target_metrics"] / uns["interactions"] → csv + 热图
```

- deconv.h5ad 缺失 → fail `ST_MISTY_NO_DECONV` 引导先跑 st_deconvolve
- extra 用 HVG 表达而非 PROGENy 通路：**离线安全**（decoupler 通路
  需联网 omnipath，破坏断网纪律；通路视图留后续联网版本）
- bandwidth 缺省 0=auto → **5 × 中位最近邻距离**（tool-misty l=5 口径）

## §4 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| n_hvg | int | 50 | extra 视图 top HVG 数；10..500，越界 INVALID_INPUT |
| bandwidth | float | 0（auto） | para 视图半径（坐标单位）；0 → 5×中位近邻距 |

## §5 产物（落 `/ws/{ds}/misty/`）

- `misty_contributions.png`：视图贡献热图（target × intra/juxta/para，
  来自 uns["target_metrics"] 贡献列）
- `misty_interactions_para.png`：para 视图 target×predictor importance
  热图（旁分泌互作——核心解读图）
- `misty_target_metrics.csv` / `misty_interactions.csv`：两 uns 表全量
- emit 键：`pngs` 聚合键 + 单名键 + n_targets / n_predictors /
  mean_gain_R2 / top_interactions（每 target para 视图 top1 预测子）
- `section_digest.py` SECTION_TITLES 加 `"st_misty": "多视图空间建模"`

## §6 错误码

| 码 | 场景 |
|---|---|
| ST_MISTY_NO_DECONV | deconv.h5ad 不存在（引导先跑 st_deconvolve） |
| INVALID_INPUT | n_hvg 越界 / bandwidth <0 / 交集 spot <100 |
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（ensure_spatial） |
| SCRIPT_ERROR | 兜底（run() 包装） |

## §7 测试

- **TDD 注册 5 用例**（`tests/unit/test_l3_st_misty.py`，同模板）：
  registered（L1_compute/timeout 1800/n_hvg 默认 50）/ forwards_params
  / defaults / ok_stripped / error_passthrough；
  `test_l3_spatial.py` 工具数断言 12→13（两处）
- **断网容器冒烟**（`scripts/_smoke_st_misty.py`）：合成 12×12 双区
  组成（左免疫/右肿瘤）deconv.h5ad + processed.h5ad 50 HVG 随机表达
  → 断言 ok、contributions 行数=细胞型数、gain_R2 全非负、para 视图
  top interaction 非空、产物 4 件齐全、pngs 键、ST_MISTY_NO_DECONV
  与 n_hvg 越界两错误路径；**liana 实测坑回填注释**
- 镜像重建（liana 层实跑）+ 全量回归（`pytest -q -m "not pg"` 门禁
  口径，1260 基线 +5 = 1265）+ pre-push 四道门 + CI 绿

## 非目标

- R mistyR 口径逐字节对齐（路线甲已拍板 liana）
- PROGENy/TF 通路视图（decoupler 联网依赖，后续可开联网增强版）
- juxtaview 单独重要性图（para 已够解读，YAGNI）
- 跨切片 importance 聚合与社群检测（tool-misty cluster_leiden——
  多切片范畴，真实多切片数据到位后再议）
