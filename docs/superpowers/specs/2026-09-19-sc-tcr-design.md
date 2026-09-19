# sc_tcr 免疫组库重建设计（Phase 70）

## §1 定位与形态

现状缺口（2026-09-19 探针 probe_sc_tcr 三判据 PASS 后立项）：

- 全树无 TCR/VDJ 工具（grep 零命中）；免疫章节是图谱 paper 标配，
  现有 sc 28 工具覆盖通讯/组成/恶性/调控/干性，独缺免疫组库；
- 方法口径已钉档（tool-tcr-startrac 技能）：filtered_contig 严格
  过滤、跨组织 clonotype `patient::canonical(cdr3s_nt)`、克隆分级
  n>=3/2/1、Startrac expa/pairwise_migr 手工公式——不依赖
  immunarch/Startrac R 包，纯 pandas/scipy 实现；
- 真机 VDJ 数据缺位（2026-09-09 注记"sc_data 无 TCR 数据"）——合成
  冒烟先行（st_integrate 先例），真机到位后补验收挂账。

落地：**新工具 sc_tcr**（sc 第 29 工具），容器脚本
`sandbox/sc_tools/tcr.py`，bio 镜像**零增重零新依赖**（纯
pandas/numpy/scipy/matplotlib）。L3 注册在 l3_singlecell（sc 亲缘），
`image=bio / script_dir` 沿用本目录默认（tcr.py 与 cellfreq.py 同
目录同模式）。

## §2 数据流

```
contig_files: [{file, patient, tissue}, ...]（≥1 份）
  ├─ 逐文件 resolve_data_path 白名单校验 → /data 挂载（sc_deconv 先例）
  ├─ 读 filtered_contig_annotations.csv(.gz)（Cell Ranger VDJ 标准列）
  ├─ 六重过滤（探针判据①口径）：
  │   is_cell & high_confidence & productive & chain∈{TRA,TRB} &
  │   raw_clonotype_id 非空 → 每细胞 TRA+TRB 双链齐 →
  │   barcode→多 clonotype_id（ambiguous）剔除
  ├─ 跨组织 clonotype（探针判据②口径）：
  │   clonotype_id = f"{patient}::{' ;'.join(sorted(set(cdr3s_nt)))}"
  │   ——同患者跨组织同序列合一、患者间前缀隔离
  ├─ clone_size 全局（跨组织）计数 + size_class: n>=3 / n=2 / n=1
  ├─ Startrac 指数（探针判据③口径）：
  │   expa：逐患者×逐组织，克隆计数的 1 - H/log2(K)
  │   migr：逐患者（tissue1/tissue2 对），Σ (clone_size/total)×H_两组织
  │         （未归一化熵，Startrac 原文口径）
  ├─ 可选写回：dataset_ref 给出时 barcode 对齐 → processed.h5ad obs
  │   增 tcr_clonotype/tcr_clone_size/tcr_size_class 三列（对齐率
  │   <50% 拒收 TCR_ALIGN_FAILED 防错配）
  ├─ 产物三件（见 §6）
  └─ emit（见 §7）
```

关键决策记录：

1. **输入 list-of-object 自包含**：`{file, patient, tissue}` 三元组
   逐文件标注——Cell Ranger 原生输出是逐样本独立 csv 且文件名无
   统一患者/组织编码（paper 的 GSM 正则是特例），把解析责任交给
   调用方最稳；st_integrate 已开 list[str] 先例，list[object] 更进
   一步，契约测试守住透传。
2. **不预合并单文件**：合并是调用方一行 pandas 的事，但 agent 侧
   无本地 pandas——多文件形态对 agent 调用更友好。
3. **expa 逐患者×逐组织全算**：paper 只取 Tumor（expa_tissue 参数
   面萎缩掉），信息完备原则——全算落表，调用方自取。
4. **migr 组织对参数化**：tissue1/tissue2 必须显式给（默认取值域
   排序前两个）；单组织数据集 migr 置 null + note（不报错——expa
   仍有效）。
5. **分组指数不做**（NI Low/High 类）：克隆分级写回 obs 后，
   sc_cellfreq（group 列）天然接管分组检验——工具组合拳哲学，
   不在大工具里重复统计层。
6. **compartment（CD4/CD8）判定不做**：paper 用 cluster 前缀正则
   属数据集特例；写回 tcr_* 列后 sc_plot 着色/sc_de 分组已覆盖
   该需求面。

## §3 stdin 契约

```json
{"contig_files": [{"file": "D:/vdj/p1_tumor.csv.gz",
                    "patient": "P1", "tissue": "Tumor"},
                   {"file": "D:/vdj/p1_pbmc.csv.gz",
                    "patient": "P1", "tissue": "PBMC"}],
 "tissue1": "", "tissue2": "", "dataset_ref": ""}
```

仅 `contig_files` 必填。

## §4 参数（ToolSpec / handler 一致）

| 参数 | 必填 | 默认 | 约束 |
|---|---|---|---|
| contig_files | ✓ | — | array of {file, patient, tissue}，≥1 |
| tissue1 | | "" | 空=取组织值域排序前两个之首 |
| tissue2 | | "" | 同上之次 |
| dataset_ref | | "" | 给出时对齐写回（可选） |

risk_level="L1_compute"；timeout_sec=1200（附录 A 1200 档，
模块常量 `_SC_TCR_TIMEOUT` 同源）。

## §5 产物（落 `/ws/tcr_<hash>/`）

- `tcr_cells.csv`：逐细胞（cell_id, patient, tissue,
  clonotype_id, clone_size, size_class）——对齐
  `tcr_final_reconstructed.csv` 约定
- `tcr_indices.csv`：expa（患者×组织）+ migr（患者，tissue1↔tissue2）
- `tcr_overview.png`：三联图——克隆大小 rank-frequency（log-log）/
  患者×size_class 堆叠柱 / expa·migr 条形

new_id：`tcr_` + 患者集合 + 文件数短 hash（`tcr_P1-P2_2f3a` 式，
re.sub 清洗 + 40 帽）。

## §6 emit 结构

`{ok, dataset_ref, n_files, n_patients, n_tissues, n_cells_total,
n_cells_kept, n_clonotypes, size_class_counts, expa: {患者×组织: 值},
migr: {患者: 值}|null, tissue_pair, overview_png, tcr_cells_csv,
tcr_indices_csv, wrote_back?, align_rate?, note}`——note 含过滤
统计（六重过滤各级剔除数）与 migr 缺席说明（单组织时）。

## §7 错误码

| 码 | 条件 |
|---|---|
| INVALID_INPUT | contig_files 空/元素缺三元组字段/必列缺失（barcode,chain,cdr3_nt,raw_clonotype_id,is_cell,high_confidence,productive） |
| TCR_NO_VALID_CELLS | 六重过滤后 0 细胞 |
| TCR_SINGLE_TISSUE_ONLY | 不报错——migr=null 降级（防御性码位保留给 emit note） |
| TCR_ALIGN_FAILED | dataset_ref 给出但 barcode 对齐率 <50% |

## §8 L3 注册与连带清单

- `l3_singlecell.py`：handler `sc_tcr`（照 sc_deconv 模板：
  `resolve_data_path` 逐文件白名单 + mounts 累加 + runner.run）
  + ToolSpec 注册（sc_cellchat_v2 之后）；handler 签名
  `sc_tcr(*, contig_files: list[dict[str, str]], tissue1: str = "",
  tissue2: str = "", dataset_ref: str = "")`——**首个 list[dict]
  参数工具**，FakeRunner 契约测试按 schema 合成 object 数组，
  断言逐元素透传
- `orchestrator/report/section_digest.py` SECTION_TITLES：
  `"sc_tcr": "免疫组库重建（TCR）"`
- 附录 A 口径表：追加 `sc_tcr` 1200 行（表守护测试强制）
- `tests/unit/test_l3_singlecell.py`：注册计数清单 28→29 +
  新增 `test_sc_tcr_dispatches_bio_image`（image/script_dir/
  timeout/list[dict] 逐元素透传断言）
- `ci.yml`：bio-image-smoke 冒烟步追加 `_smoke_sc_tcr`（env 同口径）

## §9 测试

1. **TDD（容器逻辑，宿主 pytest，mock 边界）**：六重过滤各级剔除
   计数、clonotype_id 规范化（排序去重）、expa/migr 公式（探针
   真值直接搬：0.1870927082/0.5509775004/0.4）、new_id 清洗帽、
   错误码分支——照既有容器脚本测试文件形态
2. **冒烟 `scripts/_smoke_sc_tcr.py`**（探针数据直接复用）：
   合成 4 文件（2 患者×2 组织 + 6 类污染行全埋）写临时目录 →
   场景：①跑通 + n_cells_kept=25 + n_clonotypes=9 + expa/migr
   与探针真值逐位一致 + 三产物落盘；②患者隔离（P2 复刻序列
   独立克隆）；③单文件（单组织）跑通 migr=null 降级；④contig_files
   空 → INVALID_INPUT；⑤缺 cdr3_nt 列 → INVALID_INPUT
3. **写回路径（dataset_ref 给出时）**：合成 h5ad barcode 与 contig
   部分重叠 → 对齐率护栏（<50% 拒收 / ≥50% 写回三列）两分支
4. **真机挂账**：VDJ 数据到位后补真机验收（不阻塞本 Phase 收官）

## 非目标

- 不做 immunarch/Startrac R 包集成（手工公式已对拍，零依赖原则）
- 不做分组指数汇总（sc_cellfreq 组合拳接管，见 §2 决策 5）
- 不做 BCR/IGH 重建（同构扩展留评估挂账，chain 过滤本就不含）
- 不做克隆进化/谱系树（dandelion 类，生态位另行评估）
- 不动 sc_load（contig csv 不进 h5ad 通道，数据形态正交）
