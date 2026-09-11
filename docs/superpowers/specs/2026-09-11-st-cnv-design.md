# st_cnv 空间 CNV 推断设计（Phase 46）

日期：2026-09-11
状态：用户已批准（"可行"）

## §1 定位与形态

`st_cnv`：st_* 第 10 个工具，空间转录组 spot 级拷贝数变异推断与恶性 spot 判定。

- L3 注册：`orchestrator/tools/builtin/l3_spatial.py` 新增 handler + ToolSpec（`risk_level="L1_compute"`，`timeout_sec=1800`，对齐 st_stats）
- 容器脚本：`sandbox/st_tools/cnv.py`（st 镜像，infercnvpy 已烘焙）
- 后端：**仅 infercnvpy**（与 sc_cnv 交叉验证口径一致；cnvturbo/fastCNV 不做，见非目标）

**最大化复用 sc_cnv 成熟件**（`sandbox/sc_tools/cnv.py`，2026-09-11 真机验收过）：

- GRCh38 基因坐标 TSV（镜像构建期烘焙 `/opt/cnv/gene_pos_grch38.tsv`，断网可用）
- 内置非恶性参考清单 `DEFAULT_REF_PATTERNS`（大小写不敏感子串匹配；Epithelial 故意不在默认清单）
- 基因表达预过滤 cutoff=0.1（R inferCNV 语义）+ float32 稠密化（容器 16g 内存纪律，真机 OOM 教训）
- `_attach_gene_pos` 基因组序排序（upper 归一匹配、同名歧义丢弃、<1000 报错）
- NaN 基因列防御剔除（infercnv 窗口边界输出实测带 NaN，下游 PCA 拒绝）
- 恶性判定：参考分 mean+3sd 阈值 → CNV 簇多数投票平滑
- figS3B 式染色体热图渲染（TwoSlopeNorm 中心=参考中位数）

## §2 数据流（与 sc_cnv 同构）

```
raw.h5ad (counts)  ──┐
                     ├─ obs_names 交集对齐 → cnv_ad（同源起步）
processed.h5ad ──────┘   ├─ obs 注释列（标签）
                         └─ obsm["spatial"]（坐标，ensure_spatial 校验）
```

- **counts 来源**：`load_adata({"dataset_id":..., "file":"any"})` 回退链 filtered→raw（st_load 写 raw.h5ad 存原始 counts，已核实 [load.py:137](file:///i:/飞书agent/sandbox/st_tools/load.py)）；CNV 绝不用归一化后的 processed
- **标签来源**：processed.h5ad obs 按 obs_names 交集对齐（交集 <100 spots 报错）
- **infercnvpy 侧自 normalize_total(1e4)+log1p**（其文档惯例，与 sc_cnv 相同）
- **写回**：`cnv_score` / `is_malignant` / `cnv_subclone` 三列写回 processed.h5ad（st_plot 可直接作着色/分组列）

## §3 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| annotation_key | str | `""` | spot 注释列；空则回退链 `cell_type → spatial_domain → leiden`；特殊值 `"deconv"` = 读 deconv.h5ad 取 RCTD 权重最大型作标签（生物名可命中内置非恶性清单） |
| ref_groups | list[str] \| null | null | 显式参考组覆盖（同 sc_cnv；不在列取值内报错附可用值） |
| resolution | float | 1.0 | 亚克隆 leiden 分辨率 |

参考解析零匹配 → `ST_CNV_NO_REFERENCE` fail + SystemExit(1)，**不静默降级为无参考模式**（对齐 sc_cnv 口径）。leiden 数字簇永不可能命中生物名清单，报错信息须列出可用列取值引导用户传 ref_groups 或 annotation_key="deconv"。

`deconv` 特殊值数据流：读 `/ws/{ds}/deconv.h5ad`（st_deconvolve 产物，obs 列=细胞类型权重），逐 spot 取 argmax 权重型作标签；deconv.h5ad 不存在 → fail `ST_CNV_NO_DECONV` 引导先跑 st_deconvolve。

## §4 产物（空间版独有价值：恶性区域组织定位）

落 `/ws/{ds}/cnv/`：

- `cnv_chromosome_heatmap.png`：figS3B 式染色体热图（上=参考 spot，下=恶性按亚克隆分组；复用 sc_cnv 渲染逻辑）
- `cnv_score_spatial.png` / `cnv_subclone_spatial.png`：**spatial_scatter 组织图染色**（替代 sc 版 UMAP 图；复用 st_stats 的 `_scatter_img_kwargs` 占位壳模式——无 uns["spatial"] 补壳 + img=False，`return_ax=True`）
- `cnv_celltype_summary.csv`：注释×恶性计数交叉表
- `cnv_subclone_by_chromosome.csv`：亚克隆×染色体平均偏离

emit 键：`pngs` 聚合键（**必须**——IM 发图与 D 报告共用宿主四键收集，2026-09-11 sc_cnv 验收漏图教训）+ 各单名键 + 标量（n_spots/n_malignant/malignant_ratio/threshold/n_subclones/matched_references 等，对齐 sc_cnv emit 形状）。

`orchestrator/report/section_digest.py` SECTION_TITLES 加 `"st_cnv": "空间CNV推断"`（注册表覆盖测试自动盯）。

## §5 错误码

| 码 | 场景 |
|---|---|
| INVALID_INPUT | annotation_key 不在 obs 且非 deconv / ref_groups 含不存在值 |
| ST_CNV_NO_REFERENCE | 内置清单零匹配（不静默降级） |
| ST_CNV_NO_DECONV | annotation_key="deconv" 但 deconv.h5ad 不存在 |
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（ensure_spatial） |
| SCRIPT_ERROR | 兜底（run() 包装） |

## §6 测试

- **TDD 注册 5 用例**（`tests/unit/test_l3_st_cnv.py`，对齐 st_stats 模板）：registered（L1_compute/timeout 1800）/ forwards_params（全量 dict 断言）/ defaults / ref_groups 透传 / error_passthrough（BioRunError→错误输出）；`test_l3_spatial.py` 工具数断言 9→10
- **断网容器冒烟**（`scripts/_smoke_st_cnv.py`）：合成 10×10 网格 spot 两半注释（左 "T cells" 右 "Epithelial"），右半注入 chr7 gain + chr10 loss（sc_cnv 合成测试同手法：对应染色体基因 counts 倍增/减半）；断言恶性检出富集右半（>80% 精度）、产物文件齐全、pngs 键存在、ST_CNV_NO_REFERENCE（注释列全数字簇）与 ST_CNV_NO_DECONV 路径正确
- 全量回归 + pre-push 四道门 + CI 绿收官

## 非目标

- cnvturbo 后端（sc 侧双后端交叉验证已足，st 侧单后端收敛）
- fastCNV R 环境引入（镜像体积/构建复杂度代价大）
- Visium HD bin 级 counts 聚合窗口策略（后续真实 HD 数据再议）
- spot 级纯度校正（RCTD 权重加权 CNV 信号——方法学研究范畴）
