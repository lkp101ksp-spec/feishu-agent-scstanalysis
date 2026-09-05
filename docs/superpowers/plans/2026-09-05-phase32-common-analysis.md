# Phase 32：常用分析批量迁移（toolsv1 第二批，A 类三工具）

日期：2026-09-05 ｜ 状态：实施中 ｜ 真机验收：用户决策延后，与富集分析一起统一逐个测

## 范围（用户确认：先批量加常用分析，再一个个测）

延续 Phase 31 按类分流决策，本批 3 个 A 类（Python 平替、CPU 镜像零新依赖）：

| 工具 | 对齐 toolsv1 | 实现 |
|---|---|---|
| sc_score_genes | server_gene_set_scoring（AddModuleScore/AUCell/GSVA/UCell/VISION 五法） | scanpy `tl.score_genes`（多基因集一次调用；UMAP 着色+按簇小提琴） |
| sc_metabolism | server_metabolism（scMetabolism，KEGG 通路活性） | 复用镜像 /opt/gene_sets/kegg.json 逐通路 score_genes → 簇×通路矩阵 + 热图（variance top） |
| sc_pseudotime | server_pseudotime（Monocle2/3+Slingshot+CytoTRACE2） | scanpy diffmap+DPT；root = root_marker 表达最高细胞（空=第 0 个）+ PAGA 图 |

排除本批：CellChat/MiloR/bulk 解卷积（B 类 R 镜像，待第一批使用反馈）；
SCENIC（数据库大）；CytoTRACE2（官方 py 版安装复杂）。

## 设计要点

- **多基因集打分**：`gene_sets` 参数为 object（{集名: [基因]}），一次算多集
  （对齐 AddModuleScore 批量体验）；基因与数据求交，空交集报错
- **代谢通路降维输出**：细胞×通路全矩阵只落 csv；JSON/热图只带簇均值 +
  簇间方差 top_n 通路（防响应膨胀）
- **拟时序 root 选择**：scanpy dpt 需 iroot——root_marker 参数（如 NKG7）
  取 raw 表达最高细胞作根；空则 0（并在描述中说明局限）
- 三工具均需 processed.h5ad；L1_compute；timeout 1800/1800/1200

## 文件清单

- sandbox/sc_tools/score.py / metabolism.py / pseudotime.py（新建×3）
- orchestrator/tools/builtin/l3_singlecell.py（+3 注册，共 9 工具）
- tests/unit/test_l3_singlecell.py（+3 组用例）
- sandbox/bio.Dockerfile（不变——COPY sc_tools/ 层自动带上，仅重建镜像）

## 验证

单测（注册/转发/错误透传/schema）+ 本机三脚本冒烟（同 Phase 31 模式，
WS_ROOT/GENE_SET_DIR monkeypatch）+ docker 增量重建 + 全量回归。
容器真跑与真机验收：延后到统一测试轮。
