# Phase 75 设计稿：genescore×cytosig 联读（真机脚本）+ st_genescore / st_metabolism（spot 级通路/代谢）

> 2026-09-20 · 两工作项合并设计 · 用户口径："genescore×cytosig 联读、st 侧代谢扩展等 / 做"
> 前置：Phase 72-74 已收官（74fb06a，CI 35460402127 四 job 全绿）；sc_genescore/sc_metabolism
> 已在 599sub_bbknn 之外的真机数据验证过（oscc top=JAK-STAT）。
> 依据 skill：tool-gene-program / tool-scmetabolism / tool-cytosig / tool-composition-cooccurrence。
> 挂账出处：测试总结+2026-09-19T00-12-54.md 后续建议（cytosig 联合分析 Phase、st 侧代谢/通路扩展）。

---

## 0. 两工作项定位与总原则

| 工作项 | 交付物 | 形态 | 核心新语义 |
|---|---|---|---|
| A | `_eval/real_genescore_cytosig_599sub.py` | **真机脚本**（不做工具） | PROGENy 通路活性（MLM estimate）× CytoSig 细胞因子 beta 的簇级 Spearman 联读 |
| B | `sc_tools/st_genescore.py` + `sc_tools/st_metabolism.py` | **两个新容器工具** | spot 级 PROGENy 14 通路 / KEGG 代谢活性 + 空间坐标着色图 + spatial_domain 分组 |

**总原则**：
1. **零镜像改动**：bio 镜像已齐备 decoupler 2.x + `/opt/progeny/progeny_human_top500.tsv`
   （bio.Dockerfile L284 层）+ `/opt/gene_sets/kegg.json`（L13 gene_sets 层）——
   st 双工具与 sc 版共享全部资产，工作项 B 纯 Python 层。
2. **st 前缀工具宿主沿用先例**：st_integrate / st_niche_scan / st_nichenet 均在
   sc_tools/ 且跑 bio 镜像（docker-smoke job 统一冒烟），B 完全同构，不引入 st 镜像依赖。
3. **产物目录隔离**：st 版落 `{ds}/st_genescore/`、`{ds}/st_metabolism/`，与 sc 版
   `genescore/`、`metabolism/` 互不覆盖（oscc 两套都跑时并存对照）。
4. **联读不工具化**：A 是一次性分析脚本（_eval/ 模式），口径经真机验证有结论后，
   未来如需可复用再立项工具化——避免为单一对照场景提前建抽象。
5. **反卷积加权另立 Phase**：挂账原文"RCTD 加权打分口径"中的 RCTD 在本仓工程现实是
   Cell2Location（st_tools/deconvolve.py，obsm["q05_cell_abundance_w_sf"]），且 oscc
   无 deconv.h5ad，重依赖前置，本轮明确不做（见 §8 非目标）。

---

## 1. 现状盘点（踩点结论，2026-09-20）

### 1.1 数据
- `bio_workspace/599sub_bbknn/`：processed.h5ad（~1000 细胞）+
  `cytosig/cytosig_scores.csv`（长表 `kind,factor,sample,value`；kind∈{beta,zscore}，
  sample=leiden 簇，43 因子）；**无 genescore/**——联读首步补跑。
- `bio_workspace/oscc/`：processed.h5ad（Visium spot 级，obsm.spatial +
  obs.spatial_domain（st_process key_added）+ obs.cluster_annotations 共存）；
  已有 sc 版 `genescore/`（progeny_scores/group_mean/heatmap/umap 四件套，
  当时 groupby=cluster_annotations）与 `metabolism/`；st 系工具全在其上验证过。
- `bio_workspace/pt1ksmoke/`：metabolism/ 已有（sc 版冒烟产物）。

### 1.2 工具契约（以代码为准，2026-09-20 复核）
- [genescore.py](file:///i:/飞书agent/sandbox/sc_tools/genescore.py)：
  stdin `{"dataset_id","groupby"="leiden","top_n"=14}`；资产
  `/opt/progeny/progeny_human_top500.tsv`（缺失→GENESCORE_NO_MODEL）；
  dc.mt.mlm；产物 progeny_scores.csv（首列 groupby）/ progeny_group_mean.csv /
  progeny_heatmap.png / progeny_umap.png（无 obsm['X_umap'] 则 null）。
- [metabolism.py](file:///i:/飞书agent/sandbox/sc_tools/metabolism.py)：
  stdin `{"dataset_id","method"="aucell"|"mean","groupby"="leiden","species"}`；
  库 `/opt/gene_sets/kegg.json`（human）/ `kegg_mouse.json`；
  产物 metabolism_scores.csv / metabolism_cluster_mean.csv /
  metabolism_heatmap.png / metabolism_umap.png。
- [cytosig_scores.csv](file:///i:/飞书agent/bio_workspace/599sub_bbknn/cytosig/cytosig_scores.csv)
  列结构：`kind,factor,sample,value`——联读侧直接 pivot 即可。
- L3 注册区：[l3_spatial.py](file:///i:/飞书agent/orchestrator/tools/builtin/l3_spatial.py)
  尾部（st_nichenet L863 / st_niche_scan L921 同区）；
  SECTION_TITLES 在 orchestrator/report/section_digest.py；附录A 超时行在报告附录。
- CI：docker-smoke job 逐工具冒烟步（Phase 72 sc_genescore 步为模板，
  TGFb/Glycolysis 注入真值回收判据可照抄，仅需给合成数据补 obsm.spatial 网格）。

---

## 2. 工作项 A：genescore×cytosig 联读真机脚本

### 2.1 流程（real_cytosig_599sub.py 骨架复用）
1. **补跑 sc_genescore**：host 检查 `599sub_bbknn/genescore/progeny_scores.csv`
   不存在 → `docker run --rm -i --network none` bio 镜像跑
   `sc_tools/genescore.py`（stdin `{"dataset_id":"599sub_bbknn","groupby":"leiden","top_n":14}`）。
2. **簇级对齐**：progeny_scores.csv（细胞×14，首列 leiden）按 leiden 求均值 →
   簇×14 通路矩阵；cytosig_scores.csv pivot（index=sample，columns=kind:factor）→
   簇×86（43 因子×2 kind）矩阵；两侧行（簇）取交集排序对齐。
3. **三层联读分析**：
   - **焦点对照**：PROGENy `TGFb` vs CytoSig beta 列 TGFB1/TGFB2/TGFB3/Activin A/BMP2/BMP4
     逐对簇级 Spearman（scipy.stats.spearmanr，n≈簇数 8-15，报告 rho+p）。
   - **全景矩阵**：14 通路 × 43 beta 因子簇级 Spearman 全矩阵，输出
     |rho|≥0.8 的通路-因子对清单（含方向）。
   - **CAF 叙事**：host 侧标志基因注释（Ptprc/Col1a1/Dcn/Epcam/Krt18/Lyz2/Cd3e/
     Pecam1/Acta2 → 逐簇 argmax 身份，real_cytosig 同款逻辑）→ CAF 簇的
     TGFb 簇均值排名 + TGFB 家族 beta 均值排名并列展示。
4. **产物**（`bio_workspace/_eval/`）：
   - `crossread_pathway_factor_rho.csv`（14×43 rho 矩阵 + 显著性标记）
   - `crossread_tgfb_scatter.png`（TGFb vs TGFB3 簇级散点+拟合线+rho 标注）
   - `real_crossread_summary.json`（判据逐条结果，供测试总结引用）
   - 控制台摘要段落（粘入测试总结 #20）。

### 2.2 预注册验收判据（写入脚本 assert + summary json）
- C1：TGFB 家族（TGFB1/2/3）beta 至少一员与 TGFb 的簇级 |rho|≥0.6 且方向为正
  （两工具共享 PROGENy/CytoSig 信号口径，物理上应强一致）。
- C2：CAF 簇 TGFb 簇均值排名 ≤3（TGFb×CAF 生物学预期）。
- C3：全景矩阵 ≥1 对已报道通路-因子对 |rho|≥0.8（如 TNFa 通路 vs TNFα 因子）。
- 判据不过不阻塞收口，但必须如实记录差异并给出解释假设（簇数少时 rho 方差大）。

---

## 3. 工作项 B：st_genescore / st_metabolism

### 3.1 与 sc 版的三点差异（其余逐行复用）
| # | 差异点 | 实现 |
|---|---|---|
| 1 | st 数据门槛 | 载入后校验 `obsm["spatial"]` 存在且形状 (n,≥2)，无→`INVALID_INPUT`+引导文案 |
| 2 | 主图换空间图 | obsm.spatial 散点（spot 6-8px，通路分 viridis/RdBu_r 着色）替代 UMAP 主图；UMAP 存在则附加产出 |
| 3 | 默认分组 | `groupby` 默认 `"spatial_domain"`（sc 版默认 leiden） |

### 3.2 st_genescore.py
- stdin：`{"dataset_id","groupby"="spatial_domain","top_n"=14}`（top_n 语义同 sc 版：
  每通路截断的模型基因数）。
- 模型与算法：`/opt/progeny/progeny_human_top500.tsv` + dc.mt.mlm，与 sc 版一致
  （spot 级 MLM 逐 spot 计算，无空间平滑——v1 保持与 sc 版同口径，平滑/邻域聚合
  留给未来 misty 联动）。
- 产物（`{ds}/st_genescore/`）：
  - `st_progeny_scores.csv`（首列 groupby，全 spot×14）
  - `st_progeny_group_mean.csv`（域×14）
  - `st_progeny_heatmap.png`（域×通路 z-score）
  - `st_progeny_spatial.png`（方差 top1 通路活性空间着色图；obsm.spatial 必须）
  - `st_progeny_umap.png`（可选，有 UMAP 才出）
- emit：ok / dataset_ref / groupby / n_spots / n_pathways / n_domains /
  top_by_group（top3 通路/域）/ products{...}。

### 3.3 st_metabolism.py
- stdin：`{"dataset_id","method"="aucell","groupby"="spatial_domain","species"="human"}`
  （method/species 语义与 sc 版完全一致，mouse 走 kegg_mouse.json 零成本继承）。
- 算法：AUCell 逐通路，与 sc 版一致。
- 产物（`{ds}/st_metabolism/`）：`st_metabolism_scores.csv` /
  `st_metabolism_group_mean.csv` / `st_metabolism_heatmap.png` /
  `st_metabolism_spatial.png`（top1 代谢通路空间图）/ 可选 umap。
- emit 结构同 3.2（n_pathways=KEGG 通路数）。

### 3.4 ToolSpec（双工具，l3_spatial.py 尾部注册区）
- st_genescore：参数 dataset_ref* / groupby（default "spatial_domain"）/
  top_n（int default 14）；timeout 1800；risk_level L1_compute；
  错误码透传 GENESCORE_NO_MODEL / INVALID_INPUT。
- st_metabolism：参数 dataset_ref* / method（enum aucell|mean，default aucell）/
  groupby（default "spatial_domain"）/ species（enum human|mouse，default human）；
  timeout 1800；L1_compute。
- description 写清与 sc 版边界："spot 级空间版本，要求 obsm.spatial"。
- SECTION_TITLES 增 st_genescore / st_metabolism 两行；报告附录A 各增超时行。

### 3.5 契约测试与 ci 冒烟
- 契约测试：`tests/unit/test_l3_st_genescore.py`、`tests/unit/test_l3_st_metabolism.py`
  独立文件（转发/schema/错误分支）+ test_l3_spatial.py 升级追加冒烟转发断言
  （Phase 71-74 模板）。
- ci.yml docker-smoke 增两步：
  - 合成空间网格数据（st_nichenet 冒烟模板 + obsm.spatial 网格坐标 +
    spatial_domain 列 + TGFb/Glycolysis 相关基因注入真值），
    st_genescore 断言 TGFb 注入簇回收（照抄 sc 版判据）；
  - st_metabolism 断言 Glycolysis 注入通路进 top 且 spatial 图文件落盘。

---

## 4. 真机验收（oscc）

- `docker run` 双工具跑 oscc（groupby=spatial_domain；species=human）：
  - st_genescore：spot 级 14 通路 + 域均值热图 + TGFb 空间图；
    与 sc 版（cluster_annotations 口径）TGFb/JAK-STAT 排名方向对照——
    空间域 vs 细胞注释两口径结论一致即通过（不一致如实记录）。
  - st_metabolism：域级代谢热图 + top1 通路空间图。
- 产物齐 + 联读 summary json + 测试总结 #20 回填 = Phase 75 收口。

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| oscc groupby 双列并存（spatial_domain vs cluster_annotations），st 版默认 spatial_domain 若 spot 覆盖不全则域均值稀疏 | 脚本域计数 <2 → fail INVALID_INPUT 引导换列；真机两列都可显式传 |
| 联读簇数少（599sub leiden 8-15 簇）rho 方差大，C1 判据可能 borderline | 判据不过不阻塞（§2.2），必附解释假设；可加置换稳健性参考 |
| spot 数大 AUCell 性能 | oscc 为常规 Visium 量级（数千 spot），AUCell 向量化可承受；超时 1800 预留 |
| spatial 图无 UMAP 依赖路径差异 | 空间图只用 obsm.spatial，主图路径必存在（门槛校验），UMAP 图独立 try |

---

## 6. 非目标

1. 反卷积加权打分（Cell2Location q05 权重加权通路分）——挂账另立 Phase，
   前置需在 oscc 跑一次 deconvolve。
2. 联读工具化 / L3 化——先真机出结论。
3. st 侧新物种模型（PROGENy mouse 官方即无）——genescore 系维持 human-only。
4. 空间平滑 / 邻域聚合口径（misty 联动另行设计）。
