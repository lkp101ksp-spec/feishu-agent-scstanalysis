# Phase 31：富集分析工具 sc_enrichment（gseapy，toolsv1 迁移第一批）

日期：2026-09-05 ｜ 状态：实施完成，回归/验收中

## 背景与决策

用户提供了另一平台（Shiny/R）的工具集 `toolsv1/`（65 个 server 模块），经
子代理逐文件盘点后分三类（A：Python 可平替 / B：R 金标准需容器化 /
C：已有等价不迁）。用户确认：**本批只做富集分析**，形态**按类分流**
（A 类进 bio 容器 registry 工具）。

对齐 toolsv1 能力：server_enrichment_*（clusterProfiler ORA+GSEA）→
本平台 gseapy 平替。

## 设计决策

### D1 离线基因集（容器 --network none 约束）

gseapy enrichr 在线 API 在断网沙箱不可用；MSigDB 官方直链国内 404/DNS 失败。
方案：**构建期 Enrichr 预取**（`sandbox/fetch_gene_sets.py`，docker build 时
有网）把三库存 JSON 到 `/opt/gene_sets/`，运行期离线读文件传 dict：
- hallmark = MSigDB_Hallmark_2020（50 通路）
- go_bp = GO_Biological_Process_2023（5406 通路）
- kegg = KEGG_2021_Human（320 通路）
宿主调试可用 `GENE_SET_DIR` 环境变量重定向输出。

### D2 工具面（sc_enrichment，L1_compute，timeout 1800）

流程：rank_genes_groups（指定簇）→ 上调基因（log2fc≥阈值，≤300）
ORA（gp.enrich）+ 全基因排序 GSEA（gp.prerank，min5/max500）→
ora.csv/gsea.csv + 柱状图 png（ORA -log10 adjP；GSEA NES 红正蓝负）。
基因集 enum 锁死三别名防 planner 幻觉；上调基因 <5 报错并提示调低阈值。

## 文件清单

| 文件 | 变更 |
|---|---|
| sandbox/sc_tools/enrichment.py | 新建（容器端：ORA+GSEA+绘图） |
| sandbox/fetch_gene_sets.py | 新建（构建期 Enrichr 预取） |
| sandbox/bio.Dockerfile | + gseapy 安装 + 预取层 |
| orchestrator/tools/builtin/l3_singlecell.py | + sc_enrichment 注册（第 6 工具） |
| tests/unit/test_l3_singlecell.py | + 4 用例（注册/转发/错误透传/schema enum） |

## 验证记录

- 单测 14 passed（含新 4）
- **本机全链冒烟**：造 300 细胞×324 基因两簇数据（T/B marker 注入）→
  monkeypatch WS_ROOT/GENE_SET_DIR → main() 全链过（ORA/GSEA/4 产物断言）
- **容器真跑**：镜像重建（构建期三库 50/5406/320 terms 落盘）→
  `docker run --network none` 断网跑 enrichment.py → `ok:true`，
  ORA top1 = KEGG Hematopoietic cell lineage（adj_p=0，注入的 B 细胞
  marker 命中，生物学自洽）；4 产物落宿主 workspace

## 排除项（用户确认暂不做）

- 基因集打分+代谢、拟时序+SCENIC、B 类 R 算法（CellChat/MiloR/
  BayesPrism 等）——等第一批使用反馈再排期
- 多簇批量富集（group 单簇；批量留反馈）
- GPU 镜像 st/gpu 镜像不加 gseapy（富集计算量小，CPU 足够）
