# Phase 21：空间转录组分析（st_* 工具链）设计

日期：2026-09-01
状态：已批准（用户确认设计，分 3 批交付）
前置：Phase 20 单细胞转录组分析（fc9a7d8）全部基建复用

## 1. 目标与范围

为研究代理新增空间转录组（ST）分析能力：用户通过 /research 发起 ST 分析任务，
planner 规划 st_* 工具节点 DAG，在独立 bio:st-cpu 容器内执行，分析图回传 IM
与绑定文档（复用 Phase 20 图片链路）。

**范围**（用户选择全量版，分 3 批交付）：

- 批①：基础链 5 工具（load/qc/process/markers/plot），与 sc_* 同构
- 批②：空间域增强（Banksy）+ 配体受体通讯（COMMOT）
- 批③：细胞类型反卷积（cell2location，双参考来源）

**不做**：GPU 镜像（远期）、Visium HD 亚细胞分辨率、多切片对齐、
st 数据写入文档表格以外的高级产物（生态位、轨迹等运次评估）。

## 2. 架构

```
bio:st-cpu 镜像（scanpy + squidpy + COMMOT + cell2location + torch CPU）
└── sandbox/st_tools/ 参数化脚本（stdin JSON → stdout JSON，fail() error_code 透传）
     st_load → st_qc → st_process → st_markers → st_plot   （批①）
     st_domains → st_commot                                （批②）
     st_deconvolve                                          （批③）
```

复用不动的 Phase 20 基建：

- **BioRunner**：路径白名单、dataset_ref 幂等、短命容器（-i stdin /
  --network none / --cpus / --memory）、bio_script_timeout_sec
- **工具注册框架**：L1_compute 级别、参数 schema、planner 可见性
- **超时放大**：含 st_* 节点的 plan 同样触发 research_sc_timeout_sec
  （st 节点与 sc 节点同判定集合）
- **图片回传**：research_runner 收集 png 字段 + IM image_key 上传 +
  文档三步插图（ImageBlock path 模式）

### 2.1 镜像

- 新建 `sandbox/st.Dockerfile` → `feishu-research-agent/bio:st-cpu-latest`
- 基础镜像与 bio:cpu 相同（python 3.12 slim + scanpy 栈），追加：
  squidpy、commot、cell2location、torch（CPU wheel）、leidenalg
- st 工具注册时在 ToolSpec 指定镜像名（BioRunner 已参数化镜像选择，
  Phase 20 已预留 image 参数）

### 2.2 工具定义（批①）

| 工具 | 输入 | 输出 | 脚本要点 |
|---|---|---|---|
| st_load | path（白名单内） | dataset_ref, n_spots, n_genes, format | spaceranger 目录：读 filtered_feature_bc_matrix.h5 + spatial/（tissue_positions + scalefactors），`sq.read.visium`；h5ad：校验 `.uns["spatial"]` 或 obsm["spatial"] 存在；mtx+csv：读 mtx + 坐标 csv（列名 spot,x,y），校验行数=细胞数。dataset_id：spaceranger 目录按目录内容聚合 hash（任一文件变化换 id）；h5ad/mtx 按文件路径+大小 |
| st_qc | dataset_ref, min_genes, max_genes, max_mt_pct | filtered.h5ad 路径, n_spots_kept, 分布统计 | spot 级过滤；失败消息附 genes/spot 分布（median/p90/max），沿用 sc_qc 调参模式 |
| st_process | dataset_ref, n_pcs, resolution, neighbors_key | processed.h5ad, n_domains, cluster_sizes, umap_png, spatial_png | 邻域图：squidpy 空间邻域（visium 用 hex 邻接，坐标网格推断）；PCA → Leiden（空间域=基于空间邻域的聚类）；umap.png + spatial 域着色图 |
| st_markers | dataset_ref, groupby, method | markers.json, dotplot_png | 沿用 sc_markers wilcoxon 逻辑 |
| st_plot | dataset_ref, genes (≤6), color_by | pngs[] | spatial 着色图（基因表达/域）；空间图是 st 核心输出 |

### 2.3 批② 工具

| 工具 | 输入 | 输出 | 要点 |
|---|---|---|---|
| st_domains | dataset_ref, method(banksy/leiden), resolution | domains.h5ad, n_domains, spatial_png | Banksy（squidpy 内置 `sq.gr.spatial_neighbors` + banksy 实现）；与批① Leiden 域对比 |
| st_commot | dataset_ref, species(human/mouse), dis_thr | pathway 热图 png, sender/receiver 空间图, top LR pairs | COMMOT + CellChat LR 库；dis_thr 单位 μm（visium spot 中心距 100μm，默认 200）；输出 top LR 通路 + 空间通讯图 |

### 2.4 批③ 工具

| 工具 | 输入 | 输出 | 要点 |
|---|---|---|---|
| st_deconvolve | dataset_ref, sc_ref（同任务 sc 产物 dataset_ref **或** 白名单内参考 h5ad 路径）, max_epochs | 每 spot 细胞类型比例矩阵（h5ad + json 摘要）, 比例空间着色图 | cell2location；参考需含细胞类型注释列（sc 产物用 leiden 注释列；独立参考校验 obs 注释列存在）；**独立超时参数** st_deconvolve_timeout_sec（训练慢，默认 3600s），不被 bio_script_timeout_sec 默认卡死 |

### 2.5 图片回传约定

- 新增字段约定：`spatial_png`（空间域/着色图）、`pngs`（多图列表）——
  与现有 `umap_png`/`dotplot_png` 同收集逻辑，research_runner 无需改动
  （Phase 20 已实现三类字段全收集，st 复用 pngs/spatial_png 即可）
- 文档写入：ImageBlock path 模式，复用现有注入逻辑

## 3. 数据流与幂等

```
白名单目录（BIO_DATA_ROOTS）
  └── spaceranger 目录 / h5ad / mtx+csv
        st_load → workspace/{dataset_id}/raw.h5ad（+ spatial slot）
        st_qc → filtered.h5ad；st_process → processed.h5ad
        各步产物按 dataset_ref 命名，重复执行幂等复用
```

- workspace：复用 BIO_WORKSPACE_ROOT，st 产物与 sc 产物同目录树
  （dataset_id 命名隔离）
- planner 串联场景：sc_* 产物（processed.h5ad）作为 st_deconvolve 参考
  时，通过 dataset_ref 引用同一 workspace 内文件，无需额外拷贝

## 4. 错误处理

- 路径白名单违规 → SC_PATH_FORBIDDEN（沿用错误码族，消息注明 st）
- spaceranger 目录缺 spatial/ → ST_FORMAT_INVALID（附缺失文件清单）
- h5ad 无空间坐标 → ST_FORMAT_INVALID
- 坐标 csv 行数≠细胞数 / 坐标非数值 → ST_FORMAT_INVALID
- QC 全滤光 → SC_QC_EMPTY（附分布统计指导调参）
- COMMOT 无匹配 LR 对 → ST_COMMOT_EMPTY（附物种与阈值提示）
- 反卷积参考缺注释列 → ST_REF_INVALID（附所需列名说明）
- 容器超时/非零退出/非 JSON 输出 → BioRunner 统一错误链（Phase 20 已测）

## 5. 测试策略

每批独立交付与验收，批内：

- **单测**：工具注册（st_* 全部 L1_compute、镜像指定 st-cpu、参数 schema
  透传）、load 三格式解析与坏数据拒收、幂等键、错误码透传
- **真机**：程序生成 tiny Visium 目录（~200 spots 网格、3 空间域结构、
  域特异基因 MARKER_D1/D2/D3），走 /research 全链路：
  - 批①：load→qc→process→markers→plot，IM 收到 spatial 着色图+umap+dotplot
  - 批②：domains 对比 + commot 热图与空间通讯图
  - 批③：sc 参考串联反卷积 + 独立参考路径双场景
- **回归**：全量 pytest 不破坏；sc_* 链路真机冒烟（同镜像构建变更不影响
  bio:cpu 镜像，风险低）

## 6. 交付批划分

| 批 | 内容 | 验收标准 |
|---|---|---|
| ① | st.Dockerfile + 5 基础工具 + tiny Visium 真机链路 | /research 全链路出空间着色图；幂等与错误链真机验证 |
| ② | st_domains + st_commot | 空间域对比图 + 通讯热图/空间图回传 |
| ③ | st_deconvolve + 双参考来源 + 独立超时 | 反卷积比例空间图回传；sc 串联场景真机验证 |

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| torch/cell2location 镜像构建慢、体积大（预计 +3GB） | 分层构建（st.Dockerfile 多阶段），requirements 固定版本；清华源 pip |
| cell2location 训练慢（真数据小时级） | 独立超时参数；真机用 tiny 数据（分钟级）验证链路，真数据留给用户 |
| COMMOT LR 库物种覆盖 | species 参数必填，无匹配时清晰报错 |
| squidpy visium 读取对 spaceranger 版本敏感 | tiny 数据用标准 v2 目录结构；load 捕获解析异常归一 ST_FORMAT_INVALID |
| 反卷积参考质量不可控 | 校验注释列存在 + 细胞类型数 sane check（2~30 类） |
