# Phase 76 设计：st_score_weight 反卷积加权打分（细胞型 × 通路矩阵）

日期：2026-09-21
状态：已获用户确认（brainstorming C-2 三问定案 + C-4 设计呈现通过）
前置：Phase 75（st_genescore / st_metabolism 双工具，commit 09ee762/882afe4）；st_deconvolve（cell2location，2026-09-12 真实 Visium 验收）

## 0. 背景与挂账出处

- 挂账原文（测试总结+2026-09-19T00-12-54.md L56）："反卷积权重（RCTD）加权打分口径待设计"。
- 工程现实：本仓"RCTD"一律实指 **Cell2Location q05 丰度**（sandbox/st_tools/deconvolve.py，`obsm["q05_cell_abundance_w_sf"]`）；无 RCTD/CARD/SPOTlight 实现，st-integrate spec（2026-09-19 L14）已否决引入，本 Phase 不翻案。
- Phase 75 spec §6 非目标 1 明确："反卷积加权打分（Cell2Location q05 权重加权通路分）——挂账另立 Phase，前置需在 oscc 跑一次 deconvolve"。本 Phase 即兑现。
- 顺带回应 Phase 75 Task 6 真机发现："st 域（spatial_domain）vs sc 簇（cluster_annotations）编号体系不同、方向不可直接对齐"——细胞型是跨口径统一坐标系。

## 1. 决策记录（brainstorming 三问定案）

| # | 决策点 | 选项 | 定案 | 理由 |
|---|---|---|---|---|
| 1 | 加权口径 | ①细胞型×通路矩阵 ②域级加权均值 ③两者 | **①** | 挂账本义；信息最完整；直接回答"哪类细胞驱动哪条通路"；提供跨口径统一坐标系 |
| 2 | 工具形态 | ①新独立工具 ②扩展现有两工具加开关 ③_eval 一次性脚本 | **①** | 加权是纯"两产物联接"操作；打分逻辑零重复；一个实现同服务通路/代谢两口径；职责单一 |
| 3 | 范围 | ①全包（oscc deconvolve 真机+工具+验收闭环） ②只做工具 ③复用 e9152d328675 旧 deconv 产物 | **①** | deconvolve 参数/参考现成；长任务后台跑不占交互；不留新挂账 |

其余细节（归一化口径、产物清单、错误码、测试结构）按推荐定案，见 §2-§5。

## 2. 核心算法（口径定案）

输入两路产物：

- `A = deconv.h5ad.obsm["q05_cell_abundance_w_sf"]`：spot × 细胞型丰度矩阵（st_deconvolve 落盘，列名已去前缀写入 obs 的先例见 deconvolve.py L208-212；本设计直接读 obsm 原始矩阵）
- `S = scores csv`（去首列 groupby）：spot × 通路打分矩阵（st_genescore → `{ds}/st_genescore/st_progeny_scores.csv`；st_metabolism → `{ds}/st_metabolism/st_metabolism_scores.csv`）

计算：

```
W = (Aᵀ @ S) / A.colsum()[:, None]    # 细胞型 × 通路，丰度加权均值
```

- **用丰度加权均值而非裸矩阵乘**：裸乘把"细胞多"与"活性高"混为一谈；除以各细胞型总丰度后 W 回到与 spot 分同尺度，细胞型间可比。
- 总丰度近零（colsum < 1e-6）的细胞型剔除并如实上报 `dropped_celltypes`（防 0/0 出 NaN 静默入产物）。
- spot 对齐：scores 行索引与 deconv obs_names 求交集；**重合率 <80% 拒收**（INVALID_INPUT，报实际重合数/总数，防错数据集张冠李戴）。交集内按 deconv obs_names 顺序对齐。

## 3. 工具接口与产物

- 落位：`sandbox/sc_tools/st_score_weight.py`，bio 镜像 + sc_tools 挂载（Phase 75 "零镜像改动"先例；仅需 numpy/pandas/anndata/matplotlib，均在 bio 镜像层）。
- stdin：

```json
{"dataset_id": "aaaaaaaaaaaa", "source": "st_genescore"}
```

  - `source` enum：`"st_genescore" | "st_metabolism"`——enum 定死 scores 产物路径，不给自由路径（防乱指，契约测试 _synth_args 自动覆盖）。
- 产物（落 `{ds}/st_score_weight/`，按 source 区分文件名防互相覆盖）：
  - `st_weighted_{source}_scores.csv`：细胞型 × 通路加权活性矩阵（行=细胞型，列=通路）
  - `st_weighted_{source}_heatmap.png`：列 z-score 热图（RdBu_r，vmin=-2/vmax=2，Phase 75 st_progeny_heatmap 同款口径）
- emit（products 嵌套 dict，section_digest._harvest 直接收成）：
  - `n_celltypes` / `n_pathways` / `n_spots_overlap`
  - `dropped_celltypes`（list[str]）
  - `top_by_celltype`（每细胞型 top3 通路，dict[str, list[str]]）

## 4. 错误码（沿用仓库惯例）

| 场景 | 错误码 | error_message 引导 |
|---|---|---|
| 缺 `{ds}/deconv.h5ad` | `ST_WEIGHT_NO_DECONV` | run st_deconvolve first（st_cnv/st_niche/st_misty 同款 *_NO_DECONV 惯例） |
| 缺 source 对应 scores csv | `INVALID_INPUT` | run st_genescore / st_metabolism first |
| spot 重合率 <80% | `INVALID_INPUT` | 报重合数/总数 + 两路来源 |
| source 非 enum | `INVALID_INPUT` | 列合法值 |

## 5. 测试与接线（Phase 75 同款四件套，单 commit 原子性）

1. **契约测试** `tests/unit/test_l3_st_score_weight.py`：bare mock 4 用例（注册/schema、分发透传、默认值、错误透传），模板=test_l3_st_genescore.py；L3 handler 走 BioRunner（image=bio:cpu-latest，script_dir=/opt/sc_tools），超时常量 `_ST_SCORE_WEIGHT_TIMEOUT = 600`（纯矩阵乘，远轻于打分 1800）。
2. **三守卫联动单 commit**：
   - `test_l3_spatial.py` 两处 st 名单 20→21（按字母序插入 `st_score_weight`）
   - 附录 A 超时表（2026-09-17-execution-plane-unification-design.md）600 档行追加 `st_score_weight`
   - `section_digest.py` SECTION_TITLES 追加 `"st_score_weight": "细胞型加权活性分析"`
   （三者任一缺失即有守卫测试变红，必须同 commit——Phase 75 Task 3 已验证的原子性纪律）
3. **断网冒烟** `scripts/_smoke_st_score_weight.py`：合成 deconv.h5ad（手写 obsm["q05_cell_abundance_w_sf"]，细胞型 A 集中左半网格）+ 合成 scores csv（左半 TGFb 高分注入）→ W 矩阵真值回收断言（A×TGFb 为全局最高）；三拒收场景（无 deconv / 无 scores / 低重合）。骨架照抄 _smoke_st_genescore.py（MOUNTS/BASE/DUMP_CMD 模式）。
4. **CI**：ci.yml bio-image-smoke job 在 st_metabolism 冒烟步后追加 `st_score_weight 合成冒烟`。
5. 门禁：ruff / mypy / pytest -m "not pg" 全绿后 push；远端 CI 复验（仓库已转 public 并更名 feishu-agent-scstanalysis，Actions 免费额度恢复）。

## 6. oscc 真机验收（全包闭环）

1. **前置**：oscc 跑 st_deconvolve——参数按历史定型口径（scripts/_validate_st_real_chain.py L146-148）：`sc_ref=bio_test_data/oscc_sc_ref_sub.h5ad`、`ref_label_col="cell_type"`、max_epochs=2000；CPU 量级 ref 训练 ~15min + 映射 ~2h（deconvolve.py L8-15 钉注），后台长任务执行。
2. **验收**：st_score_weight 分别吃 st_genescore（14 通路）与 st_metabolism（315 通路）oscc 产物跑通，产物落 `bio_workspace/oscc/st_score_weight/`。
3. **生物学 sanity 判据**（预注册，不过不阻塞，如实记录）：
   - S1：Epithelial cells 的 top3 通路含 JAK-STAT / EGFR / TGFb 家族成员之一（依据：2026-09-12 病理交叉验收 Epithelial 富集 SCC 区 1.604×；Phase 75 Task 6 各域 top3 以 JAK-STAT/EGFR/TNFa 为主）；
   - S2：加权矩阵的 "Epithelial × TGFb" 排名高于其在中性算术均值口径下的排名或持平（加权不应颠覆既有方向）；
   - S3：dropped_celltypes 为空或仅含已知低丰度型（如实记录）。
4. 结果回填测试总结 #21（含细胞型坐标系对照，回应 Task 6 编号不可对齐痛点）。

## 7. 非目标

1. 恶性纯度校正变体（CNV 加权等）——方法学研究范畴，沿用 st-cnv spec（2026-09-11 L85）挂账口径。
2. 空间平滑 / 邻域聚合口径——misty 联动另行设计（Phase 75 §6 非目标 4 同款）。
3. RCTD / CARD / SPOTlight 实现——st-integrate spec 已否决，不翻案。
4. st_deconvolve 工具本身任何改动——零改动纯复用。
5. sc 侧（599sub 等）加权——sc 数据无 spot 维度，口径不成立。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| oscc deconvolve ~2h 长任务失败/超时（历史三次超时+僵尸容器事件） | 参数沿用验收过的定型口径；后台执行+轮询；失败如实记录不阻塞工具交付（工具冒烟/契约为独立交付物） |
| scores csv 与 deconv.h5ad spot 错位（不同数据集产物混指） | 重合率 <80% 硬拒收 + 报数 |
| q05 丰度列结构变动（cell2location 0.1.5 前缀坑，测试总结+2026-09-02 L86-138） | 直接读 obsm["q05_cell_abundance_w_sf"] 原始矩阵，不依赖 obs 列名；缺键时 ST_WEIGHT_NO_DECONV 引导重跑 |
| 全零细胞型致 NaN | colsum<1e-6 剔除 + dropped_celltypes 上报 |
