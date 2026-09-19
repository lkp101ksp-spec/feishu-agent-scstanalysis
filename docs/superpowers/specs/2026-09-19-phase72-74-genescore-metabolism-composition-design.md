# Phase 72-74 设计稿：sc_genescore（PROGENy MLM）/ sc_metabolism AUCell 升级 / sc_cellfreq 共现网络升级

> 2026-09-19 · 三阶段合并设计 · 用户口径："依次做，sc_genescore（PROGENy/decoupler 通路活性打分）、
> sc_metabolism（代谢通量定量）、组成偏好分析（Ro/e + 共定位网络）"
> 前置：Phase 71 sc_cytosig 已收官（3e81a4a，CI 35435285914 四 job 全绿）。
> 依据 skill：tool-gene-program / tool-scmetabolism / tool-composition-cooccurrence。

---

## 0. 三件套定位与总原则

| 阶段 | 工具 | 动作 | 核心新语义 |
|---|---|---|---|
| 72 | `sc_genescore`（**新工具**） | 新建 | PROGENy 加权网络 MLM（estimate+perm 无关的解析 p 值），14 通路活性 |
| 73 | `sc_metabolism`（**升级**） | 就地升级 | method='aucell'（默认，对齐 scMetabolism 官方 AUCell）/ 'mean'（Phase 32 legacy 保留） |
| 74 | `sc_cellfreq`（**升级**） | 增量升级 | Spearman 共定位/共现网络（|rho|>0.6 & BH q<0.05 边 + 社区 + authority） |

**总原则**：
1. **共享基础设施一次落地**：decoupler pip 层（72 的 MLM + 73 的 AUCell 双用）、
   PROGENy 全模型 CSV 资产（72 专用）、igraph/networkx 已在镜像（74 零新依赖）。
2. **五连带 + 冒烟 + CI + 真机** 全链路，每阶段独立 commit，顺序 72→73→74。
3. **运行期断网铁律**：PROGENy 网络以 repo 资产入库 + Dockerfile COPY 烘焙
   （构建期零网络，避 2026-09-19 zenodo 504 教训）；KEGG 库已有烘焙层。
4. **宿主 venv 零污染**：decoupler 兼容性探针全部在 docker 容器内做
   （`python:3.12-slim` + numpy 2.5.2 同构环境），不进 pyproject/requirements-lock
   （宿主不 import decoupler；ADR-0028 锁文件纪律因此不触发）。
5. 72/73 用 raw 层（use_raw=True 惯例，与 metabolism.py/score.py 现状一致）。

---

## 1. 现状盘点（踩点结论，2026-09-19）

- 镜像主环境：`python:3.12-slim` 逐层 pip（numpy 2.5.2 / scanpy 1.12.4 / anndata
  0.13.3 / scipy 1.18.1 / statsmodels 0.15.0 / networkx 3.6.1 / igraph 1.0.0）；
  **decoupler 缺位**——72 首步探针钉版。
- `/opt/gene_sets/` 已烘焙：kegg.json（human）/ kegg_mouse.json / hallmark /
  go_bp / wikipathways_mouse（fetch_gene_sets.py，Enrichr 源，构建期有网）。
- [metabolism.py](file:///i:/飞书agent/sandbox/sc_tools/metabolism.py)：Phase 32 实现=
  逐通路 `sc.tl.score_genes` 均值差（对齐 scMetabolism 概念但**非官方 AUCell**）；
  输出 metabolism_scores.csv / cluster_mean.csv / heatmap / umap，emit schema 成熟。
- [score.py](file:///i:/飞书agent/sandbox/sc_tools/score.py)：sc_score_genes=自定义
  基因集 AddModuleScore（无权重、无通路 p 值）——**与 PROGENy MLM 语义不同，
  不复用不改造，72 平行新建**。
- [cellfreq.py](file:///i:/飞书agent/sandbox/sc_tools/cellfreq.py)：已有比例表+堆叠柱+
  卡方+Fisher OR/BH+Ro/e 热图+fate_bias+donor 级 MW-U（2 组）；
  **缺 Spearman 共现网络**（74 的全部新增面）。
- 五连带接线点模板（Phase 71 定型）：
  ①容器脚本 sc_tools/*.py ②L3 handler+ToolSpec（l3_singlecell.py 尾部注册区）
  ③SECTION_TITLES（orchestrator/report/section_digest.py L22）④附录A 超时行
  ⑤契约测试（tests/unit/test_l3_sc_cytosig.py 独立文件 / test_l3_singlecell.py 升级追加）。
- 既有契约测试：test_l3_singlecell.py L281（metabolism 转发）、L1075（species_mouse）、
  L601/L618（cellfreq 转发/donor）——73/74 升级时**同步更新这些断言**。
- CI：bio-image-smoke job 内逐工具冒烟步（ci.yml L159 cytosig 步为模板）；
  runner 无层缓存，每次裸构建——新 pip 层走清华源直连，COPY 资产零网络。

---

## 2. Phase 72：sc_genescore（PROGENy/decoupler 通路活性）

### 2.1 语义与边界
- PROGENy 14 通路（EGFR/MAPK/NFkB/JAK-STAT/PI3K/TGFb/TNFa/Trail/VEGF/Androgen/
  Estrogen/Wnt/p53/Hypoxia）× 加权目标基因（mor∈[-1,1]）→ 逐细胞 MLM：
  `estimate ~ mor`，通路活性=estimate+解析 p 值（对比 sc_score_genes 的无权重均值差，
  对比 sc_cytosig 的置换显著性——三者互补，ToolSpec description 写清边界防误调）。
- 物种：**v1 仅 human**（PROGENy 官方模型即 human）；species 自动检测为 mouse 或
  显式传 mouse → INVALID_INPUT + 引导文案。

### 2.2 PROGENy 全模型资产（本地化，skill 口径 progeny::getModel 的 Python 等价）
- 资产：`sandbox/gene_sets/progeny_human.csv`（列 source/target/mor；**全量模型**
  非 top500——运行期按 top 参数截断，资产一次入库永久免网）。
- 生成途径（探针附录B 按序尝试，宿主/容器一次性动作，产物人工抽查 14 通路名）：
  ① decoupler `dc.get_progeny(organism='human', top=大数)` → ② saezlab/progeny
  GitHub `data/model.rda` 经 pyreadr 读出 → ③ 容器内 R 无关兜底不做（镜像无 R 需求）。
- Dockerfile：独立薄层 `COPY gene_sets/progeny_human.csv /opt/gene_sets/`。

### 2.3 容器脚本 sc_tools/genescore.py
流程：args → load_adata(processed) + leiden 校验 → 物种守卫（复用
species_style_guard，human 强制）→ 读 /opt/gene_sets/progeny_human.csv →
top 截断（每通路按 |mor| 降序取前 top）→ 与 raw.var_names 求交
（matched<200 → die GENESCORE_LOW_OVERLAP）→ decoupler MLM（探针钉 API：
1.x `dc.run_mlm(mat, net, source=, target=, weight='mor', min_n=5)` /
2.x `dc.mlm(...)`，封装 `_mlm()` 薄适配）→ estimate/pvals 两矩阵（细胞×14 通路）。
产物（WS_ROOT/<ds>/genescore/）：
- `genescore_scores.csv`（全矩阵首列 leiden）
- `genescore_pvals.csv`
- `genescore_cluster_mean.csv`
- `genescore_heatmap.png`（簇×通路 z-score，方差排序，RdBu_r）
- `genescore_umap.png`（方差 top1 通路活性 UMAP 着色）
emit：ok / dataset_ref / species / n_cells / n_pathways=14 / matched_genes /
top_by_group（每组 beta 降序 top3 通路）/ top_paths（方差 top5 簇均值摘要）/
products{scores_csv,pvals_csv,cluster_mean_csv,heatmap_png,umap_png}。

### 2.4 ToolSpec
- 参数：dataset_ref* / groupby*（leiden 或注释列，簇均值与 top_by_group 用）/
  top（int，default 500，min 100 max 1000）/ species（enum ["human",""]，留空
  自动检测且非 human 报错）。浅层 schema（Phase 70 教训）。
- timeout 1800；risk_level L1_compute；错误码透传 GENESCORE_LOW_OVERLAP。

---

## 3. Phase 73：sc_metabolism AUCell 升级

### 3.1 改动面（metabolism.py 就地升级，输出 schema 向后兼容）
- 新参 `method`：`'aucell'`（**默认**，对齐 scMetabolism 官方 AUCell——skill
  tool-scmetabolism 口径：countexp 矩阵直入、无插补）/ `'mean'`（Phase 32
  score_genes 原路径原样保留，回归保障）。
- AUCell 实现：kegg.json/kegg_mouse.json → 长表 net(target=基因, source=通路)
  → decoupler `run_aucell`（与 72 共享 decoupler 层）→ estimate 矩阵替换
  score_genes 列采集环。**aucMaxRank 对齐本项目 skill 口径（前 10% 基因）**：
  探针确认 decoupler 默认值，能显式传则显式传（`nup`/aucmaxrank 形态探针钉注），
  不能则代码注释钉档默认口径。
- mouse upper_gene_map / MIN_PATHWAY_GENES=5 / 产物文件名全部不动；
  emit 追加 `"method"` 字段 + note（默认口径变更钉档，老口径传 method='mean'）。

### 3.2 连带更新
- ToolSpec description 补 method 说明 + parameters 加 method 枚举；
- test_l3_singlecell.py：L281/L1075 组补 method 默认值与转发断言。

---

## 4. Phase 74：sc_cellfreq 共现网络升级

### 4.1 改动面（cellfreq.py 增量，Ro/e/Fisher/donor 全部不动）
- 新参：`cooccurrence`（bool，default true）/ `min_rho`（number，default 0.6）。
- 触发条件：`cooccurrence=true` 且 **by 样本数 ≥ 5**（Spearman 最低功效线），
  否则 emit `cooccurrence=None` + note；与 group 是否两值无关（样本级分析）。
- 算法（tool-composition-cooccurrence skill 口径）：
  1. props（样本×簇比例）逐簇对 `scipy.stats.spearmanr` → rho/p 矩阵；
  2. 上三角对 → statsmodels BH → 边集 `|rho|>=min_rho 且 q<0.05`，带 signed_rho；
  3. igraph Graph（顶点=簇，weight=|rho|）：`authority_score()` 排序 +
     `community_edge_betweenness(weights=1-|rho|)` 社区（rho 大→距离小→先合并）；
  4. `layout_circle()` + matplotlib 画 circular 网络：节点按社区着色、
     边宽∝|rho|、红正蓝负、无边孤立节点灰圈。
- 产物（cellfreq/ 目录）：`cooccurrence_edges.csv`（source,target,rho,q,n_samples）
  / `cooccurrence_communities.csv`（celltype,community,authority）/
  `cooccurrence_network.png`。
- emit 增 `cooccurrence`{n_edges, n_communities, top_edges[3], edge_csv,
  comm_csv, network_png, note}；**note 必钉组成闭合效应提醒**（props 和为 1 的
  封闭数据，Spearman 反映共享丰度模式，非因果、负边慎读）。

### 4.2 连带更新
- ToolSpec 加两参数（boolean/number 浅 schema）；timeout 600 不变；
- test_l3_singlecell.py L601/L618 组补 cooccurrence/min_rho 默认值与转发断言。

---

## 5. Dockerfile / CI / 门禁

### 5.1 bio.Dockerfile（两处新增，均在 71 cytosig 层后、非 root 用户前）
```dockerfile
# Phase 72/73 通路活性与代谢通量：decoupler（72 run_mlm / 73 run_aucell
# 双后端）。版本锁 B-2 探针钉注（numpy 2.5.2 兼容 + API 形态）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple decoupler==<探针版> \
    && python -c "import decoupler; print('decoupler ok')"

# Phase 72 PROGENy 全模型资产（repo 内置，构建期零网络；运行期断网可用）
COPY gene_sets/progeny_human.csv /opt/gene_sets/progeny_human.csv
```

### 5.2 ci.yml（bio-image-smoke job，cytosig 步后追加三步）
```
sc_genescore 合成冒烟（TGFb 注入断层+mouse 拒收+低重叠拒收）
sc_metabolism 合成冒烟（Glycolysis 注入 aucell/mean 双口径）
sc_cellfreq 合成冒烟（共变注入共现网络+Ro/e 回归+小样本降级）
```

### 5.3 门禁（每阶段提交前）
ruff + `python -m mypy`（涉及平台符号时加 `--platform linux`，本设计无——纯
Python 数据流）+ pytest 全量（现 1350 + 新增）。冒烟脚本本机实跑后推 CI。

---

## 6. 冒烟设计（三脚本，统一 cytosig 冒烟骨架：合成 h5ad → docker run
--network none + sc_tools 整目录挂载 → 断言 JSON）

### 6.1 scripts/_smoke_sc_genescore.py
合成：1000 基因（TGFb 通路 top100 取自容器侧 dump 的 progeny_human.csv）×
40 细胞（A/B 各 20），A 群注入 2.0×TGFb 签名加权向量（seed=7）。
①默认参数 → ok + n_pathways=14 + TGFb ∈ top_by_group["A"] top3 且断层
（beta ≥ 其余通路最大值×1.5）+ 四产物落盘；
②species="mouse" → INVALID_INPUT；
③600 个 FAKE 基因 → GENESCORE_LOW_OVERLAP。

### 6.2 scripts/_smoke_sc_metabolism.py
合成：KEGG Glycolysis 基因集注入 A 群（容器侧 dump kegg.json 取集）。
①method 缺省 → emit method=="aucell" + Glycolysis ∈ A 簇 top3；
②method="mean" 显式 → 回归绿（Phase 32 行为不变）；
③mouse 数据 + method=aucell → 正常走 upper_gene_map（回归）。

### 6.3 scripts/_smoke_sc_cellfreq.py
合成：8 样本 × 4 簇；簇1/簇2 比例共变注入（目标 rho≈0.9），簇3/4 独立噪声；
样本标签拆 4:4 两 group 保 Ro/e 回归面。
①默认 → edges 含 (簇1,簇2) 且 rho>0.6、q<0.05；不含独立对 (簇3,簇4)；
communities/authority 非空 + network png 落盘；
②Ro/e 回归：fate_bias 与 fisher_q 字段照旧存在；
③3 样本（<5）→ cooccurrence=None + note 降级。

---

## 7. 验收标准

1. 门禁三绿（ruff/mypy/pytest 全量含新增契约测试）。
2. 三冒烟脚本本机绿 + CI run bio-image-smoke 对应三步 success。
3. 真机验证（B-8，遵守大库纪律 ~1000 细胞子集）：599sub 数据集三工具全跑通，
   且 **交叉闭环**：CAF 簇 PROGENy TGFb 通路活性排名 vs Phase 71 CytoSig
   TGFB3/TGFB1 rank1/2 结论方向一致（论文级交叉印证）。
4. ROADMAP 加 72/73/74 三行；测试总结（测试总结+2026-09-19T00-12-54.md）逐段追加；
   每阶段独立 commit + push。

---

## 8. 风险与预案

| # | 风险 | 预案 |
|---|---|---|
| 1 | decoupler 与 numpy 2.5.2 不兼容 | 容器内探针先钉；1.x/2.x 双 API 适配层；双败兜底=statsmodels 手写加权 MLM（MLM 语义=逐通路加权秩回归，可自实现；AUCell 兜底=手写秩瀑布 AUC，两者均为纯 numpy） |
| 2 | PROGENy 全模型三途径全败 | 资产入库是 72 硬前置，挂账阻塞并明示；降级方案=只提交 top500 版（get_progeny 默认）并锁 top 参数 |
| 3 | run_aucell 默认 aucmaxrank 与 10% 口径不符 | 探针源码确认；能传参则显式 10%，不能则注释钉档默认值与偏差说明 |
| 4 | 共现网络组成闭合效应误读 | emit note 硬编码提醒；README 口径只在 skill 复现场景引用 |
| 5 | 73 默认口径变更伤存量调用 | method='mean' 全保留；emit method 字段自明；契约测试双向覆盖 |
| 6 | CI runner 无层缓存 | decoupler 层清华源直连（纯 Python 轮）；COPY 资产零网络；无编译链需求 |

---

## 附录A 超时与参数档

| 工具 | timeout | 新增参数 |
|---|---|---|
| sc_genescore | 1800 | dataset_ref* / groupby* / top(500, 100-1000) / species(["human",""]) |
| sc_metabolism | 1800（不变） | method(["aucell","mean"]，默认 aucell) |
| sc_cellfreq | 600（不变） | cooccurrence(bool, true) / min_rho(number, 0.6) |

## 附录B B-2 首步容器内探针清单（全绿后才动镜）

```bash
docker run --rm python:3.12-slim bash -c "
  pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple numpy==2.5.2 decoupler &&
  python - <<'PY'
  import decoupler, numpy, inspect
  print('decoupler', decoupler.__version__, '| numpy', numpy.__version__)
  print('has run_mlm', hasattr(decoupler,'run_mlm'), '| has mlm', hasattr(decoupler,'mlm'))
  print('has run_aucell', hasattr(decoupler,'run_aucell'), '| has aucell', hasattr(decoupler,'aucell'))
  print('has get_progeny', hasattr(decoupler,'get_progeny'))
  # 小合成钉注：20 基因×30 细胞×2 通路 MLM+AUCell 跑通与输出形态
  PY"
```
钉注项：①版本与 numpy 共存 ②MLM/AUCell API 形态与输出（AnnData obsm vs tuple）
③run_aucell aucmaxrank 默认 ④get_progeny 全量试拉 → 生成 progeny_human.csv 资产
（第②③④步任一形态确认后写死进 genescore.py/metabolism.py，不留运行时分支）。
