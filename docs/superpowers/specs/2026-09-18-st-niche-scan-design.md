# st_niche_scan 全 niche NicheNet 批量扫描设计（Phase 68）

## §1 定位与形态

st_nichenet（Phase 65）一次只吃一个 receiver_niche + 一份显式 geneset；
Phase 67 探针 `bio_workspace/_eval/probe_oscc_allniche.py` 验证了
"枚举全 niche → 逐 receiver 数据驱动 geneset → 43×5 aupr 矩阵 + 三源
交叉 summary"的编排价值（5/5 全绿、25 文件零覆写、空间约束改写全图
排序）。本设计将该探针升格为正式 L3 工具 **st_niche_scan**。

形态：**薄编排容器脚本** `sandbox/sc_tools/st_niche_scan.py`——
不重写 NicheNet 主链，循环内以 subprocess 自调同目录
`st_nichenet.py`（`Path(__file__).with_name("st_nichenet.py")`，
镜像内 `/opt/sc_tools/` 与本地挂载同名，双部署成立）。
st_nichenet.py 是真机验证资产，**零改动**；slug 产物/KB 桥/绘图/
species guard 全部复用。跨镜像分发沿 Phase 57/65 先例：物理在
sc_tools/、bio 镜像执行，st 镜像零增重。

## §2 数据流

```
processed.h5ad（读一次，内存常驻）
  ├─ 枚举：obs[groupby] value_counts ≥ min_spots 的 niche 列表（保序按计数降序）
  ├─ 循环每个 niche：
  │    ├─ 自算 geneset（niche_up_genes 同口径，见 §3）
  │    ├─ subprocess: python st_nichenet.py < stdin JSON（timeout=剩余预算帽 1800）
  │    └─ 成功 → results[niche]；超时/非零退出/stdout 非法 JSON → failures[niche]，continue
  ├─ 汇总：aupr 宽表 + 热图 + summary JSON
  └─ emit（含每 niche top_ligands / failures / 产物路径）
```

时间预算：外层墙钟 = ToolSpec timeout_sec（3600s），脚本自管
`BUDGET = 3600 - 180`（收尾余量）；每 niche subprocess timeout =
`min(1800, 剩余-60)`；剩余 < 300s 时停止枚举，剩余 niche 记
`failures[niche] = {"error_code": "TIME_BUDGET"}`，照常汇总 emit。
全 niche 失败才 `fail("ST_NICHESCAN_ALL_FAILED", ...)`。

## §3 geneset 口径（对拍锁定）

与探针 `niche_up_genes` **逐位同口径**，保证 p68-5 真机对拍可逐值
比对：raw 快照（`a.raw.X` 优先回退 `a.X`）、niche 内 vs 其余 spot 的
normalized-log 均值差降序、剔 `MT-`/`RPL`/`RPS` 前缀、取 top
`n_geneset`（默认 30）。niche 内 spot 数 < min_spots 不入枚举
（与探针 MIN_SPOTS=5 默认一致）。

## §4 stdin 契约

```json
{"dataset_id": "oscc", "groupby": "spatial_domain", "min_spots": 5,
 "n_geneset": 30, "max_rings": 1, "knn": 6, "species": "human",
 "top_n_ligands": 30, "min_expr": 0.1}
```

除 `dataset_id` 外全可选，默认同 st_nichenet ToolSpec。

## §5 参数（ToolSpec / handler 一致）

| 参数 | 必填 | 默认 | 约束 |
|---|---|---|---|
| dataset_ref | ✓ | — | 12-hex |
| groupby | | spatial_domain | obs 列 |
| min_spots | | 5 | ≥1 |
| n_geneset | | 30 | 10..100 |
| max_rings | | 1 | 1..5 |
| knn | | 6 | 4..20 |
| species | | ""（自动探测） | human/mouse |
| top_n_ligands | | 30 | 5..100 |
| min_expr | | 0.1 | 0..1 |

risk_level="L1_compute"；timeout_sec=3600（模块常量
`_NICHE_SCAN_TIMEOUT`，handler `runner.run(..., timeout_sec=` 同源，
契约测试自动钉死）。niche 数多时由调用方预期墙钟（description 注明
分批或 `min_spots` 调高）。

## §6 产物（落 `/ws/{ds}/`）

- 每 niche：st_nichenet 原生 5 件（`*_{slug}.csv/png`，slug 规则复用）
- 汇总三件：
  - `nichenet_allniche_matrix.csv`：ligands × niches aupr 宽表（aupr 原值，不做 z-score，探针同口径）
  - `nichenet_allniche_heatmap.png`：同数据热图
  - `nichenet_allniche_summary.json`：niches 元数据（n_spots/n_sender/ring_counts/geneset 前 10）、每 niche top_ligands、failures

## §7 emit 结构

`{ok, n_spots, groupby, n_niches_total, n_niches_ok, n_niches_failed,
niches: [...], results: {niche: {n_sender, n_geneset_used,
n_ligands_tested, top_ligands, ligand_activities_csv}}, failures:
{niche: {error_code, error_message}}, matrix_csv, heatmap_png,
summary_json}`——克制：完整表靠落盘文件，emit 只带 top 与计数。

## §8 错误码

| 码 | 条件 |
|---|---|
| ST_NICHESCAN_NO_GROUP | groupby 列不存在 |
| ST_FORMAT_INVALID | 缺 obsm.spatial / X scaled 且无 .raw |
| INVALID_INPUT | 枚举后 niche 空（min_spots 过严） |
| ST_NICHESCAN_ALL_FAILED | 全 niche 失败 |
| TIME_BUDGET | 剩余预算不足被跳过（failures 内，非顶层 fail） |
| NICHE_TIMEOUT | 单 niche subprocess 超时（failures 内，非顶层 fail） |

前置校验一次（h5ad 可读 / groupby / spatial / X 口径），不让每个
niche 重复报同类错。

## §9 L3 注册与连带清单

- `l3_spatial.py`：新增 handler（照 st_nichenet L337 模板：
  resolve_species 注入 + `image=bio_image, script_dir=_SC_SCRIPT_DIR,
  timeout_sec=_NICHE_SCAN_TIMEOUT` + BioRunError→_err + pop ok）+
  ToolSpec 注册（st_nichenet 之后）
- `orchestrator/report/section_digest.py` SECTION_TITLES：
  `"st_niche_scan": "全 niche NicheNet 配体扫描"`
- 附录 A 口径表（2026-09-17-execution-plane-unification-design.md
  L113 的 3600 行）：追加 `st_niche_scan`（表守护测试强制）
- 契约测试零改动：通用遍历自动入列（timeout/cpus/memory 三断言）

## §10 测试

1. 冒烟 `scripts/_smoke_st_niche_scan.py`（ci.yml 追加一步，沿
   st_nichenet 冒烟格式，复用合成网格 build 与 `_nnpeek` 先验导出）：
   - ① `stnnscan` 三 niche（R/S/F）全跑：n_niches_ok==3、R 的 top10
     含 A2M（空间自证）、汇总三件落盘、failures 空
   - ② min_spots=1000 → INVALID_INPUT（枚举空）
   - ③ groupby="nope" → ST_NICHESCAN_NO_GROUP
   - ④ 无 spatial 库 → ST_FORMAT_INVALID
2. 真机对拍（p68-5）：工具版对 oscc 全 niche vs
   probe_oscc_allniche 的 alln_summary.json——5 niche top_ligands
   aupr 逐值一致（同口径 subprocess 应严格可复现）

## 非目标

- 不改 st_nichenet.py 一行（subprocess 黑盒复用）
- 不做 per-niche 并行（串行保序，预算模型简单；并行留 Phase 后续）
- 不做热图 z-score / 交叉 cellchat-commot（后者留在探针侧，工具只
  出 NicheNet 自身矩阵）
- 不动 KB 桥 nichenetr_bridge.R
