# st_trajectory 空间拟时序设计（Phase 51）

日期：2026-09-12
状态：用户已批准（"好的，按这个方案来"；root_mode 双模式按推荐甲案）

## §1 定位与形态

st_* 第 14 工具：空间转录组扩散拟时序（DPT）——scanpy diffmap +
DPT + PAGA 在**表达邻居图**上推断 spot 进程序，结果映射回组织
空间坐标，回答"表达进程是否沿空间（侵袭）方向展开"。

- 容器脚本：`sandbox/st_tools/trajectory.py`（sc_tools/pseudotime.py
  Phase 32 模式移植：UMAP 散点 → 组织空间散点；leiden 分组 →
  spatial_domain）
- **镜像零改动**：st 镜像 scanpy/squidpy 1.8.3 已在；st_process
  产物 DPT-ready（uns['neighbors'] 表达图 + obsm['X_pca'] +
  obs['spatial_domain']，process.py L36-46）
- 注册：l3_spatial.py 第 14 个 handler + SECTION_TITLES 38 项

## §2 关键选型（与 sc 版差异的取舍依据）

- **表达邻居图跑 DPT**（sc 语义直通）：空间图 DPT≈BFS 距离场，
  与 st_vicinity（Phase 48 shortest_path 分层）语义重复，不做
- **PAGA groups=spatial_domain**（st 原生域），无该列回退 leiden；
  PAGA 图节点=域**空间质心**（非 UMAP 质心）
- **空间特色**：obs 有 vicinity 列（st_vicinity 写回）时，附
  pseudotime~vicinity 层 boxplot + Spearman ρ——st 独有的
  "表达进程×空间分层一致性"可解读指标；无 vicinity 不报错
  （vicinity 仅 root_mode=vicinity 时才是硬依赖）

## §3 数据流

```
processed.h5ad（uns['neighbors'] 表达图 + obsm['spatial']）
  root_mode=marker  → root=root_marker 表达最高 spot（raw 无则 X；
                      空/缺失 → spot #0 + root_note 注明）
  root_mode=vicinity → obs['vicinity']==root_layer 的 spot 中取表达
                      邻居度中位者；列缺失 → ST_TRAJ_NO_VICINITY
  adata.uns['iroot']=root → sc.tl.diffmap → sc.tl.dpt
  dpt_pseudotime（inf=不连通 → NaN + n_disconnected 计数）
  sc.tl.paga(groups=spatial_domain|leiden) → 域间连接度
        ↓
/ws/{ds}/trajectory/：pseudotime.csv + trajectory_spatial.png（组织
散点 viridis 着色+红圈 root）+ paga_spatial.png（域空间质心+
连接度加权边）+ [trajectory_vicinity.png（boxplot+ρ，有 vicinity 时）]
```

## §4 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| root_mode | str | "marker" | 定根模式：marker ｜ vicinity；非法 INVALID_INPUT |
| root_marker | str | "" | marker 模式：基因 symbol，表达最高 spot 为根 |
| root_layer | str | "tumor" | vicinity 模式：根所在层（tumor/distal/L1..Ln）；层无 spot INVALID_INPUT |

## §5 产物与 emit

- 四件产物见 §3（trajectory_vicinity.png 条件产出）
- emit：ok/dataset_ref/method="diffmap_dpt"/n_spots/root_mode/
  root_marker/root_cell_index/root_note/n_disconnected/per_domain
  [{domain, mean, median, n_spots}]/有 vicinity 时 spearman_rho+
  spearman_pval/产物路径键（pseudotime_csv/trajectory_png/paga_png
  [/vicinity_png]）/note

## §6 错误码

| 码 | 场景 |
|---|---|
| ST_TRAJ_NO_VICINITY | root_mode=vicinity 但 obs 无 vicinity 列（先跑 st_vicinity） |
| INVALID_INPUT | root_mode 非法 / root_layer 无 spot / processed 缺 uns['neighbors'] 或 obsm['spatial']（先跑 st_process） |
| ST_FORMAT_INVALID | processed.h5ad 不可读（load_adata 既有） |
| SCRIPT_ERROR | 兜底 |

## §7 测试

- **TDD 注册 5 用例**（`tests/unit/test_l3_st_trajectory.py`）：
  registered（L1_compute/timeout 600/root_mode 默认 marker）/
  forwards_params（args dict 一键不多）/defaults/BioRunError 透传/
  st_* 总数 13→14（test_l3_spatial.py 两处）
- **断网容器冒烟**（`scripts/_smoke_st_trajectory.py`）：12×12 网格
  144 spot，60 基因表达沿 x 坐标线性渐变+噪声（builder 容器内
  sc.pp.neighbors 建表达图）；root=高表达端 marker → DPT 应恢复
  梯度序（断言 pseudotime~x Spearman>0.9）；obs 注入合成
  vicinity 层 → vicinity 模式 root=tumor 跑通 + vicinity_png 产出
  + ρ 断言符号方向；错误路径：NO_VICINITY（删掉列再跑 vicinity
  模式）/INVALID_INPUT（root_mode=bogus）
- 全量回归（`pytest -q -m "not pg"` 门禁口径，1267 基线 +5）
  + pre-push 四道门 + CI 绿

## 非目标

- 分支推断/BEAM/CytoTRACE2/Monocle3（sc 版同口径排除）
- 空间图 DPT（与 st_vicinity BFS 语义重复）
- RNA velocity（需未剪切矩阵，st 数据链无此前置）
- Palantir/CellRank/stlearn PSTS（重依赖，YAGNI）
- 多切片联合拟时序（单切片内语义先行）
