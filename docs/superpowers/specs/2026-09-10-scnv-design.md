# sc_cnv 设计：拷贝数变异推断与恶性判定（双后端 infercnvpy / cnvturbo）

日期：2026-09-10
状态：设计已获用户批准（对话内分节设计稿 "可以，继续"）；已实施（见 ROADMAP 2026-09-10 行与测试总结追加六）
前置：Phase 20-37（24 个 sc_* 工具，全库 mypy strict 已收官）

## 1. 背景与决策

生信扩展方向评估（2026-09-10 对话）四选二，用户决策：**B1 CNV 推断先行、D 报告汇编随后**；
TCR 免疫组库砍掉，GEO/TCGA 数据获取与 RNA velocity 不做。

CNV 算法库选型（用户决策：**甲丙双后端都要**）：

- **甲 infercnvpy 0.6.1**（默认）：inferCNV 的 Python 官方重实现，scanpy 生态原生，
  h5ad 进出零搬运，社区广泛引用，成熟度最高；
- **丙 cnvturbo 0.3.0**：自称对齐 R inferCNV HMM i6 管线、快约百倍，scanpy/AnnData
  原生集成；版本尚早（0.1.0→0.3.0），作为第二后端与甲互为交叉验证；
- **乙 R 原版 inferCNV 否掉**：h5ad↔R 搬运成本撑不起算法增益（r_tools 虽有 R 先例）。

brainstorming 需求澄清结论：

| 维度 | 定案 |
|---|---|
| 基线模式 | 有参考模式（免疫/基质做基线，inferCNV 经典打法） |
| 参考细胞来源 | 内置默认清单 + `ref_groups` 显式覆盖；零匹配报错，不静默降级 |
| 物种 | 人，GRCh38 坐标（GRCm39 留扩展位不实现） |
| 交付物 | 全量：obs 写回（cnv_score/is_malignant）+ 染色体热图 + 亚克隆聚类 |

## 2. 范围

新增 `sc_cnv` 工具：一次调用完成全基因组 CNV 推断 → 每细胞/每簇打分 →
恶性判定 → 恶性细胞亚克隆聚类 → 热图/UMAP/审阅表。

**不做**（YAGNI）：TCR 免疫组库（用户砍掉）、CNV 与生存/临床关联、
真实临床样本验证（后续拿公开肿瘤数据集人工核对，另行安排）、小鼠坐标、
连坐改造 plot.py。

## 3. 工具设计

### 3.1 参数契约

| 参数 | 类型/默认 | 说明 |
|---|---|---|
| `dataset_ref` | str，必填 | 需先 sc_process；标签列来自 processed.h5ad |
| `method` | `"infercnvpy"`（默认）\| `"cnvturbo"` | 算法后端 |
| `celltype_col` | str，默认 `"leiden"` | 参考细胞来源列（celltypist_label/annotation 等注释列亦可） |
| `ref_groups` | list[str]，可选 | 显式参考类型；缺省走内置清单匹配 |
| `resolution` | float，默认 1.0 | 亚克隆 leiden 分辨率 |

窗口/cutoff 等 R 兼容参数（window_size=101、min_mean_expr_cutoff=0.1、
apply_2x_transform=True、排除 chrX/Y）**内部定死不暴露**——算法保真参数
不是分析选择。

### 3.2 数据流（照 doublet.py 先例）

```
filtered/raw 回退链 → counts（全基因矩阵）
processed.h5ad     → celltype_col 标签（按 obs_names 交集对齐进 counts）
双后端同源起步（交叉验证可比性前提）：
  infercnvpy 路：normalize_total(1e4)+log1p → tl.infercnv(reference_key/cat)
  cnvturbo  路：infercnv_r_compat(raw_layer=counts, R 兼容参数)
结果 obs 列 reindex(adata.obs_names) 写回 processed.h5ad
```

processed.h5ad 无 counts 层（X=scaled HVG 子集、raw=log-norm 快照），
CNV 需全基因 counts，故从 filtered/raw 回退链取（doublet.py 同款）。

### 3.3 基因坐标注入

- 构建期 `fetch_gene_pos.py` 下载 Ensembl GRCh38 GTF.gz → 只留 gene 级
  symbol/chromosome/start/end → 压成 ~2MB TSV 烘进 `/opt/cnv/gene_pos_grch38.tsv`
  （仿 fetch_gene_sets.py / celltypist 模型预取先例，断网可用）；
- 运行期按基因符号 upper 归一匹配注入 `var["chromosome"/"start"/"end"]`
  （upper 桥接 + 歧义丢弃，仿 common.upper_gene_map 语义）；
  保留 chr1-22/X/Y，未定位基因剔除并计数报告，定位基因 <1000 报错；
- **主要风险点**：两库底层约定 var 三列即可（from_gtf 本质也是填这三列），
  但 cnvturbo 文档只展示 from_gtf 路线——实现第一步先双后端各跑冒烟验证
  var 注入路线；若 cnvturbo 强制走自家 from_gtf，回退方案：烘 GTF.gz 全文件
  （~50MB，镜像可接受）。

### 3.4 参考细胞匹配（默认 + 可覆盖）

- 内置清单（人源常见非恶性，**大小写不敏感子串匹配**，"T cell" 命中
  "CD4 T cells"）：T cell、B cell、NK、Macrophage、Monocyte、Dendritic、
  Neutrophil、Fibroblast、Endothelial、Pericyte、Smooth muscle、Erythrocyte；
- **Epithelial 故意不进默认清单**（可为恶性来源），用户可靠 ref_groups 显式加；
- emit 报 `matched_references` 与全部注释取值，匹配透明可查；
- 显式 ref_groups → 直接用，值不存在时报错列出可用取值（`_cat_cols` 自纠风格）；
- **零匹配 → 报错**（错误码 SC_CNV_NO_REFERENCE），消息给出 ref_groups
  写法示例，不静默降级为无参考模式。

### 3.5 恶性判定（两后端各自语义，输出列统一）

- **infercnvpy 路**：X_cnv 上 PCA→邻居→leiden 得 CNV 簇 → 簇级 cnv_score；
  **阈值 = 参考簇分数分布 mean+3sd**，超阈簇判恶性 → 细胞随簇得
  `is_malignant`。簇级判定比逐细胞稳（CNV 逐细胞噪声大是共识）；
- **cnvturbo 路**：`compute_hspike_emission_params` 校准 →
  `hmm_call_subclusters`（HMM i6）直接给细胞级 Normal/Tumor →
  `is_malignant = (cnv_call == "Tumor")`；
- 统一写回 obs：`cnv_score`（细胞级，X_cnv 绝对偏差均值）、`is_malignant`
  （bool）、`cnv_call`（仅 cnvturbo 路）、`cnv_subclone`（恶性细胞内 leiden，
  C1..Cn；非恶性填 "non-malignant"；恶性细胞 <50 时退化为单克隆并 note 说明）。

### 3.6 产物（落 `/ws/{ds}/cnv/`）

- `cnv_chromosome_heatmap.png`：经典染色体×细胞热图（按 is_malignant 分组、
  亚克隆排序；infercnvpy pl.chromosome_heatmap / cnvturbo cnv_pl 同名接口）；
- `cnv_score_umap.png` / `cnv_subclone_umap.png`：复用 processed 的 X_umap；
- `cnv_celltype_summary.csv`：**注释类型×恶性判定计数**（最关键审阅表：
  哪类细胞被判定恶性）；
- `cnv_subclone_by_chromosome.csv`：亚克隆×染色体平均信号（后续 D 报告
  汇编的直接素材）。

## 4. 依赖与镜像

- bio.Dockerfile 新独立层（保上方缓存层）：

```dockerfile
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple infercnvpy cnvturbo
COPY fetch_gene_pos.py /tmp/fetch_gene_pos.py
RUN python /tmp/fetch_gene_pos.py && rm /tmp/fetch_gene_pos.py
```

- 构建期探针：装完跑 `python -c "import infercnvpy, cnvturbo"`（scTenifoldKnk
  先例）；Ensembl 不可达时 build 报错重试（fetch_gene_sets 同语义）；
- 纯 CPU 工具，不进 GPU 镜像、不走 _accel()；sc_cnv 恒用 runner 默认
  镜像（bio:cpu-latest）；
- cnvturbo 无 Windows 原生支持——工具跑 Linux 容器，与本机无关；
- 带原生编译依赖风险（infercnvpy 若引 pybiomart/gtfparse 链）在构建层
  预检，按 bbknn 层先例同层临时装 g++ 后 purge。

## 5. 注册与测试

- [l3_singlecell.py] +1 ToolSpec（24→25 工具），handler 沿用
  `runner.run + _err + pop ok` 模式；timeout 3600s（同 cellchat/milo/scenic），
  handler 内 runner.run timeout_sec 必须一致（防静默回退 900s）；
- risk_level=L1_compute；description 按惯例写清：需先 sc_process（最好
  已 sc_annotate）、ref_groups 用法、结果列名供 sc_plot/sc_de 分组引用；
- 单测（test_l3_singlecell.py +3-4 用例）：注册断言 / L1_compute /
  参数转发（method/celltype_col/ref_groups 进 payload）/ timeout 一致；
- 容器集成测试（test_sandbox_docker.py，docker marker）：合成小数据
  （免疫参考群 + 人工造 CNV 信号的"恶性"群——若干条染色体基因整体上调
  1.5-2x）→ sc_qc→process→cnv 双后端 → 断言 is_malignant 与造的标签吻合、
  obs 列与产物文件齐全；
- 全量回归（basetemp 指向 TEMP 下专用目录，勿混入其他产物——2026-09-10
  basetemp 纪律）；Docker 起后补跑 docker marker 项；
- 五文档同步：ROADMAP / 平台功能说明书（25 工具表）/ 使用说明书（触发
  话术）/ 测试总结 / 本 spec 状态行。

## 6. 文件清单

| 文件 | 动作 |
|---|---|
| sandbox/sc_tools/cnv.py | 新建（双后端分派 + 统一后处理） |
| sandbox/fetch_gene_pos.py | 新建（构建期 GTF→TSV） |
| sandbox/bio.Dockerfile | 加 infercnvpy+cnvturbo 层与坐标烘焙 |
| orchestrator/tools/builtin/l3_singlecell.py | +1 ToolSpec |
| tests/unit/test_l3_singlecell.py | +3-4 用例 |
| tests/integration/test_sandbox_docker.py | +CNV 端到端用例（docker marker） |
| docs/ROADMAP.md / 平台功能说明书 / 使用说明书 / 测试总结 | 同步 |
