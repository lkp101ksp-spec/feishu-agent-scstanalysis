# st_nichenet 空间配体活性优先级设计（Phase 65）

日期：2026-09-18
状态：用户已批准（"可以，st做起来"）；前置探针 `bio_workspace/_eval/probe_st_nichenet2.py` 三路判据全过

## §1 定位与形态

`st_nichenet`：st_* 第 16 个工具。空间转录组版 NicheNet 配体活性优先级——
在 sc_nichenet（Phase 60）基础上引入**空间邻域约束**：sender 定义为
receiver niche 的 kNN BFS 一环邻域，min_expr 门控落在 sender 侧，
实现"邻近才通讯"的空间语义（sc 版是全图任意群配对，st 版收窄到物理接壤）。

- L3 注册：`orchestrator/tools/builtin/l3_spatial.py` 新增 handler +
  ToolSpec（`risk_level="L1_compute"`，`timeout_sec=1800`——探针实测
  Visium ~12 niche 逐对循环 15–25 min，入 1800 档）
- **跨镜像分发（Phase 57 st_cellchat_v2 先例）**：nichenetr R 桥单点
  安装在 bio 镜像 → 脚本放 `sandbox/sc_tools/st_nichenet.py`
  （**非 st_tools**），handler `image=bio_image, script_dir=_SC_SCRIPT_DIR`
  ——st 镜像零增重；经同一 WS_ROOT 卷直读 st processed.h5ad

## §2 数据流

```
processed.h5ad ── obsm["spatial"]（ensure_spatial）→ cKDTree k+1 近邻图
   │                                                   ↓
   │ obs[groupby]                               spatial_rings BFS 分环
   │ receiver_niche → core 掩码                  0=core / 1..max_r=环 / -1=远端
   │                                                   ↓
   └── X（负值回退 .raw）              sender = r1..max_r ∩ min_expr 门控
                                       receiver = core
                                            ↓
                        expr_stats.csv + geneset.csv（KB 轻量桥，矩阵不出 Python）
                                            ↓
                        Rscript /opt/r_tools/nichenetr_bridge.R（零改动复用）
                                            ↓
                        activities.csv + links.csv + pngs（含 rings.png）
```

- **sender 侧门控即空间约束**：仅与 receiver niche 物理接壤（BFS r1）
  且表达过阈的 spot 入 sender 集——sc 版的群级任意配对收窄为邻域配对
- KB 桥零改动：nichenetr_bridge.R 输入输出契约与 sc_nichenet 完全一致

## §3 空间分环方法（探针已验证）

`scipy.spatial.cKDTree(coords).query(coords, k=knn+1)` → 邻接表 →
自 receiver core 掩码做 BFS 逐环扩散（deque，环计数 ≤ max_rings）。
分环开销毫秒级。探针三路判据（50165a559c91_bbknn 真数据）：

1. 空间侧：A2M（真实空间配体）活性 rank1；诱饵基因 0/5 入选
2. 非空间对照：诱饵 5/5 tested（候选 48→53）——空间约束确有过滤
3. 洗牌对照：5/5 排序稳定——结果非坐标噪声

## §4 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| geneset | list[str] | 必填 | receiver 侧目标基因集，minItems 5 |
| receiver_niche | str | 必填 | receiver 分组取值（obs[groupby]==该值 的 spots 为 core） |
| groupby | str | "spatial_domain" | 分组列名 |
| max_rings | int | 1 | BFS 环数上限；sender = r∈[1, max_rings] |
| species | str | "" | human/mouse，空=自动探测（L3 resolve_species 注入） |
| top_n_ligands | int | 30 | 报告前 N 配体 |
| min_expr | float | 0.1 | sender 侧表达比例门控（空间约束的生物学落点） |
| knn | int | 6 | kNN 图近邻数 |

越界：geneset∩数据基因 <5 / sender 空 / 可测配体 <3 → INVALID_INPUT

## §5 产物（落 `/ws/{ds}/nichenet/`）

- `nichenet_ligand_activities.csv` / `links.csv`（sc_nichenet 同名同构）
- `bar.png`（top 配体活性条形）/ `heatmap.png`（配体×靶点权重热图）
- `rings.png`：spatial 分环着色图（空间工具身份产物，占位壳模式）
- emit 键：`pngs` 聚合键（IM/D 报告纪律）+ top_ligands / n_sender /
  n_receiver / ring_counts
- `section_digest.py` SECTION_TITLES 加
  `"st_nichenet": "空间配体活性优先级（NicheNet）"`
- 执行面 spec 附录 A 超时表：`st_nichenet` 入 1800 档

## §6 错误码

| 码 | 场景 |
|---|---|
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（ensure_spatial） |
| ST_NICHENET_NO_GROUP | obs 无 groupby 列 / receiver_niche 取值不存在 |
| INVALID_INPUT | geneset 交集 <5 / sender 空 / 可测配体 <3 / species 无法判定 |
| SCRIPT_ERROR | 兜底（run() 包装） |

## §7 测试

- **TDD 注册 5 用例**（`tests/unit/test_l3_st_nichenet.py`，
  st_cellchat_v2 模板）：registered（L1_compute / timeout 1800 /
  bio 镜像 + /opt/sc_tools/ 断言）/ forwards_params / defaults /
  ok_stripped / error_passthrough；`test_l3_spatial.py` 工具数
  断言 15→16（两处）
- 合同测试自动入列（`_synth_args` 按 schema 合成）+ 超时表守护
  三方比对（registry / skill / settings vs 附录 A）自动覆盖
- **断网容器冒烟**（`scripts/_smoke_st_nichenet.py`，
  _smoke_sc_nichenet 模板）：合成空间数据（三区布局 + A2M 真实
  空间配体 + 诱饵基因）→ 断言 A2M rank1 / 诱饵被过滤 / 产物齐全 /
  pngs 键 / 三错误路径（无 spatial / 无 groupby / geneset 交集不足）；
  步骤 0 先验 RDS 导出真基因名（PEEK 目录惯例）
- ci.yml bio-image-smoke job 追加冒烟步骤
- 全量回归（`pytest -q -m "not pg"` 门禁口径）+ pre-push 四道门 + CI 绿

## 非目标

- 多 niche 逐对全矩阵（receiver_niche 单值参数化；逐对循环留给
  报告层 L4 编排，避免单工具 12×12 放大）
- COMMOT / MISTy 空间通讯积分（独立工具已覆盖）
- ligand-target 先验网络自定义（nichenetr 默认 KB）
- sender 侧细胞亚型拆分（spot 级分辨率下无意义）
