# sc_integrate Harmony 引擎 + st_integrate 多切片整合设计（Phase 69）

## §1 定位与形态

现状缺口（2026-09-18 调研）：

- `sc_integrate`（Phase 33）仅 bbknn 单方法——`integrate.py` 容器侧
  完全未读 `method`，ToolSpec `enum=["bbknn"]`；
- 全树无 Harmony / SCTransform（grep 零命中）；st 侧 17 工具全部
  单切片，多切片 merge+整合无工具（真实项目刚需，pdac-spatial
  pipeline 类工作流的核心环节）；
- harmonypy 为纯 Python pip 包（numba 由 scanpy 层自带），无编译、
  无 R 栈——bio 镜像 bbknn 层同款轻量加层；
- RCTD 方向生态位与已有 `st_deconvolve`（cell2location）重叠，不立。

双落地：**sc_integrate 增 method="harmony"**（第二整合引擎）+
**新工具 st_integrate**（st 第 18 工具，多切片 merge→整合→表达域
聚类）。st_integrate 物理在 `sandbox/sc_tools/`、l3_spatial 注册
`image=bio / script_dir=_SC_SCRIPT_DIR`——沿 st_nichenet/st_niche_scan
跨镜像分发先例，st 镜像零增重。

形态说明：st_integrate **非** st_niche_scan 式薄编排——多数据集
merge 这步无存量工具可复用（integrate.py 只吃单 dataset_id），
subprocess 黑盒模式不适用；整合段（HVG→PCA→harmony→neighbors→
UMAP→leiden）与 integrate.py 同构，独立实现，integrate.py 的
bbknn 路径零改动。

## §2 sc_integrate harmony 增量（p69-2）

容器 `integrate.py`：

- `method = args.get("method", "bbknn")` 读入 + 校验
  `in ("bbknn", "harmony")`，非法 raise ValueError；
- `import bbknn` 延迟到 bbknn 分支内（harmony 用户不硬性依赖
  bbknn——错误消息只在真正调用时抛）；
- bbknn 分支照旧（`bbknn.bbknn(..., neighbors_within_batch=nwb)`）；
- harmony 分支：`scanpy.external.pp.harmony_integrate(adata, key=batch)`
  （obsm `X_pca` → `X_pca_harmony`）→
  `sc.pp.neighbors(adata, use_rep="X_pca_harmony", n_neighbors=...)`；
  两分支汇聚后 UMAP/Leiden 共用；
- `new_id = {parent}_{method}`（bbknn 行为逐字节不变 = `{parent}_bbknn`）；
- emit：`method` 已有；`neighbors_within_batch` 仅 bbknn 分支携带。

handler `sc_integrate`（l3_singlecell）**零改动**——`method` 本就
透传 payload，timeout 1800 不变。ToolSpec：`enum=["bbknn","harmony"]`
+ description 更新（提及双方法与 harmony 走 X_pca_harmony）。

镜像 `bio.Dockerfile`：bbknn 层后追加 harmonypy pip 层（清华源，
无编译依赖）：

```dockerfile
# Phase 69 整合引擎二：harmonypy（纯 python，numba 由 scanpy 层带）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple harmonypy
```

## §3 st_integrate 数据流与关键决策

```
dataset_refs（≥2 个 st 数据集 ref）
  ├─ 逐片 load_adata(file="any")：filtered.h5ad → raw.h5ad 回退链
  │   （st 与 sc 同模式；全基因 counts 形态）
  ├─ anndata.concat(label=slice_col, keys=refs)：
  │   obs 增批次列（默认名 "slice"，值为各 ref id）；
  │   基因 outer join（缺失补 0）；obsm["spatial"] 有则保留
  ├─ 统一预处理（各片独立 HVG 有交集偏差，全局统一更标准）：
  │   normalize_total(1e4) → log1p → HVG(n_top_hvg, seurat) →
  │   raw 快照 → scale(max_value=10) → PCA(n_pcs, arpack)
  ├─ 整合：harmony（默认，X_pca→X_pca_harmony→neighbors）/
  │   bbknn（neighbors_within_batch 同 sc_integrate 口径）
  ├─ UMAP + leiden（resolution）
  │   ★ 不做空间邻域图——跨切片坐标无意义，这是与 st_process 的
  │     核心差异；st_integrate 的 leiden 是"表达域"，下游空间分析
  │     按切片回各原始数据集做
  ├─ spatial_offset=true 时：各片 obsm["spatial"] 累积平移并排
  │   （gap = 前片 x_max + 10% x 跨度；默认 false 保原始坐标）；
  │   emit note 钉注：offset 后跨切片 spot 在空间 kNN 工具
  │   （st_stats/st_nichenet 等）中会被误判为邻居，offset 仅展示用
  ├─ 产物三件（见 §6）
  └─ emit（见 §7）
```

关键决策记录：

1. **输入走 filtered→raw 回退链而非 processed**：processed 已是
   HVG 子集+scaled，各片 HVG 不同，merge 交集有生物偏差且 scaled
   数据不能重 normalize；counts 形态统一重预处理是 harmony 教程
   标准口径。调用前提：各片至少跑过 st_load（raw），建议 st_qc
   （filtered）。
2. **基因 outer join 补 0**：counts/log 形态下 0 = 未检测，语义
   无害；join="inner" 会在片间基因面板差异大时塌缩。
3. **slice 列值 = ref id**：可读、可回溯，天然防同名混淆。
4. **new_id 命名**：`{ref1}__{ref2}[_etc]__{method}`（>2 片加
   `_etc`），`re.sub(r"\W+","_")` 清洗 + 80 长度帽——双片场景
   可读，多片防爆长。

## §4 stdin 契约

```json
{"dataset_refs": ["sliceA", "sliceB"], "method": "harmony",
 "n_top_hvg": 2000, "n_pcs": 30, "n_neighbors": 15,
 "resolution": 1.0, "slice_col": "slice", "spatial_offset": false}
```

仅 `dataset_refs` 必填。

## §5 参数（ToolSpec / handler 一致）

| 参数 | 必填 | 默认 | 约束 |
|---|---|---|---|
| dataset_refs | ✓ | — | array of string，≥2 |
| method | | harmony | harmony/bbknn |
| n_top_hvg | | 2000 | ≥100 |
| n_pcs | | 30 | 5..100 |
| n_neighbors | | 15 | 5..100 |
| resolution | | 1.0 | >0 |
| slice_col | | slice | 合法 obs 列名 |
| spatial_offset | | false | bool |

risk_level="L1_compute"；timeout_sec=1800（模块常量
`_ST_INTEGRATE_TIMEOUT`，handler 同源，契约测试自动钉死）。

## §6 产物（落 `/ws/{new_id}/`）

- `processed.h5ad`：obs（slice 列 + leiden + 质控列并集）、
  obsm（X_pca / X_pca_harmony 或 bbknn 邻居 / X_umap / spatial 有则
  保留）、raw（log 后全基因快照）
- `umap_integrated.png`：左按 slice_col 着色（混合程度）、右按
  leiden——sc_integrate 双联图同款
- `spatial_slices.png`（条件产物：全片有 spatial 时）：spot 空间
  分布按 slice 着色（offset 开启时自然并排）

## §7 emit 结构

`{ok, dataset_ref, parent_refs, method, slice_col, n_slices,
slice_sizes: {slice: n}, n_cells, n_genes, n_clusters,
cluster_sizes, umap_png, spatial_png?, spatial_offset, note}`——
note 含下游提示（表达域语义 + offset 误用钉注）。

## §8 错误码

| 码 | 条件 |
|---|---|
| INVALID_INPUT | dataset_refs 缺失/<2/含重复；method 非法；slice_col 与 obs 现有列冲突 |
| ST_INTEG_REF_MISSING | 某片无 filtered/raw 可读 |
| ST_INTEG_NO_OVERLAP | 合并后 HVG 为 0（基因面板完全不相交） |
| ST_INTEG_SINGLE_BATCH | 合并后 slice 列仅 1 个取值（理论不可达，防御） |

镜像缺 harmonypy → RuntimeError（"rebuild bio image with harmonypy
pip layer"，bbknn 同款句式）。

## §9 L3 注册与连带清单

- `l3_spatial.py`：新增 handler（照 st_niche_scan 模板：
  `image=bio_image, script_dir=_SC_SCRIPT_DIR,
  timeout_sec=_ST_INTEGRATE_TIMEOUT` + BioRunError→_err + pop ok）+
  ToolSpec 注册（st_niche_scan 之后）；
  handler 签名 `st_integrate(*, dataset_refs: list[str], ...)`——
  首个 list 参数工具，FakeRunner 契约测试按 schema 合成参数需确认
  list 类型透传（如有坑按 test_st_plot_genes 先例处理 repr/list 双
  形态）
- `orchestrator/report/section_digest.py` SECTION_TITLES：
  `"st_integrate": "多切片空间数据整合"`
- 附录 A 口径表（2026-09-17-execution-plane-unification-design.md
  1800 行）：追加 `st_integrate`（表守护测试强制）
- `tests/unit/test_l3_spatial.py`：注册计数清单 17→18（fourteen
  函数历史遗留名不动，只改清单）；新增
  `test_st_integrate_dispatches_bio_image`（image/script_dir/
  timeout/list 透传三断言）
- `tests/unit/test_l3_singlecell.py`：sc_integrate enum 断言
  （如有既有 spec 快照测试同步 enum=["bbknn","harmony"]）
- `ci.yml`：st 冒烟步后追加 `_smoke_st_integrate`（env 同口径）

## §10 测试

1. **TDD（容器逻辑，宿主 pytest，mock 边界）**：new_id 清洗/长度帽、
   refs 校验（<2/重复）、错误码分支——照既有容器脚本测试文件形态
2. **冒烟 `scripts/_smoke_sc_integrate_harmony.py`**（合成双批）：
   两批各 300 细胞 × 500 基因，批 B 全基因均值 +0.5 偏移 + 批特异
   标记基因两枚；场景：①harmony 跑通 + new_id `_harmony` 后缀 +
   n_batches=2 + 两图落盘；②批特异标记对应簇在 leiden 中分离
   （自证整合未抹掉生物信号）；③method="nope" 拒收
3. **冒烟 `scripts/_smoke_st_integrate.py`**（合成双切片）：
   两片各 20×20 网格（空间结构：片内左右两域差异基因），片 B 全局
   偏移；场景：①跑通 + slice_sizes {A:400,B:400} + n_clusters≥2 +
   三产物落盘；②spatial_slices.png 存在；③refs 单片拒收
   INVALID_INPUT；④ref 不存在拒收 ST_INTEG_REF_MISSING
4. **真机 p69-5（大库纪律：~1000 子集 seed=7）**：
   - sc 侧：59900（50165a559c91_bbknn 源库抽子集，供体/批次列
     保留）分别跑 bbknn vs harmony → 混合度对照（简化 iLISI：
     30-NN 异批比例均值）+ leiden 簇数/ARI 对照——BBKNN 有历史
     产物可横向比
   - st 侧：oscc（1749 spots）按 x 坐标中位数切两半存双 h5ad →
     st_load ×2 → st_integrate → 两半混合度 + 表达域结构
     （对照 Phase 66 单切片 8 域口径）

## 非目标

- 不动 integrate.py 的 bbknn 路径一行（harmony 为纯增量分支）
- 不做 SCTransform（R/Seurat 栈，收益与 normalize_total+HVG 差异
  对本工具链场景不关键，留评估挂账）
- 不做 st 空间域的跨切片对齐/映射（leiden 为表达域；空间对齐
  属 PASTE 类方法，另行评估）
- 不动 st_deconvolve / cell2location（RCTD 生态位重叠不立）
- harmony 调参（lambda/theta）不暴露——默认值已覆盖主流场景
