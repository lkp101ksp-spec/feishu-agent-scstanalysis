# Phase 53：sc_pseudotime 增强——动态基因趋势 + root_cluster 定根

日期：2026-09-13　状态：已批准（用户"可以"）

## 1. 背景与缺口

sc_pseudotime（Phase 32，[pseudotime.py](../../../sandbox/sc_tools/pseudotime.py)）
已交付 diffmap+DPT+PAGA 全链：root_marker 定根 → DPT 伪时序 →
UMAP/PAGA 图 → 分簇统计。头注释自标范围外缺口：分支推断 / BEAM
动态基因 / CytoTRACE2。st_trajectory（Phase 51）已把同款 DPT 模式
移植到空间侧（含 root_mode 双模式先例：marker/vicinity）。

用户指令"sc_trajectory 拟时序"→ 校准为既有基座能力升级
（AskUserQuestion 被用户跳过，按"都要，合并一轮"先例合并两枚
高性价比升级）。

### 缺口两枚

1. **动态基因趋势**：DPT 只给细胞顺序，不给"哪些基因沿顺序动态
   变化"——Monocle BEAM 的核心解读场景缺失。
2. **定根盲区**：仅 root_marker（需用户猜得到基因名），空则回退
   cell#0 基本不可用；agent 最自然的表述是"以某簇为根"。

## 2. 目标 / 非目标

### 目标

- **动态基因趋势（"BEAM-lite"）**：HVG 池内逐基因
  Spearman(dpt_pseudotime, raw 表达) + BH 校正 → top N 动态基因；
  产物三件套 dyn_genes.csv / trend_heatmap.png / trend_curves.png。
- **root_cluster 定根**：指定 leiden 簇 → 簇内邻居图度最高细胞
  定根；与 root_marker 互斥校验（同给 INVALID_INPUT）。

### 非目标（挂账，各自独立 Phase 先探针）

- CytoTRACE2 / Palantir / RNA velocity：均需新 pip 依赖 + 模型或
  剪接层快照，断网容器纪律要求构建期快照，单独评估。
- 分支推断（branch assignment / 分支特异基因）：DPT 分支语义弱，
  价值/成本比一般。

## 3. 参数设计（stdin 新增两参数，现状默认不变）

| 参数 | 默认 | 语义 |
|---|---|---|
| `dyn_top_n` | `50` | 动态基因分析 top N；`0`=跳过（纯 Phase 32 行为） |
| `root_cluster` | `""` | leiden 簇名 → 簇内度最高细胞定根；与 root_marker 互斥 |

- 候选基因池：`adata.var["highly_variable"]` 为 True 且存在于
  raw.var_names 的基因（sc_process 产物必有 HVG 列；缺失则
  INVALID_INPUT 引导先跑 sc_process）。
- 互斥：root_marker 与 root_cluster 同给 → INVALID_INPUT
  （错误消息写明二选一）。
- root_cluster 不在 leiden 取值中 → INVALID_INPUT（列出可用簇）。

## 4. 实现设计

### 4.1 pseudotime.py（重构 main + 新增两函数）

- `_degree_root(adata, mask) -> int`：簇内定根——取邻居图
  connectivities 子矩阵行和（度）最大的细胞全局索引。
- `_dyn_genes(adata, pt, top_n, ds_dir) -> dict`：
  1. 候选池 = HVG ∩ raw.var_names；逐基因取 raw 表达（稀疏兼容）；
  2. `scipy.stats.spearmanr(pt, expr)`（pt 非有限细胞先掩掉）→
     rho/pval；`statsmodels.stats.multitest.multipletests(method=
     "fdr_bh")` 校正 qval；
  3. 按 |rho| 降序、qval<0.05 过滤取 top_n → dyn_genes.csv
     （gene/rho/pval/qval 全量写盘，emit 只带 top10 摘要）；
  4. trend_heatmap.png：细胞按 pt 升序，top 基因行 × 细胞列，
     每基因移动平均平滑（窗=max(10, n//50)）后 z-score，imshow
     （cmap="viridis"，无刻度轴，顶部色条标 pt）；
  5. trend_curves.png：top6（|rho| 最大 6 个）2×3 子图，
     散点下采样 500 细胞 + 移动平均曲线。
- main 分支：定根三段（root_marker 现状 / root_cluster 新 /
  皆空 cell#0 现状回退），root_note 写明模式；dyn_top_n>0 时
  追加 `_dyn_genes` 段；emit 加 root_mode/dyn 段（n_dyn、top_dyn、
  三产物路径）。
- 统一 fail("INVALID_INPUT") 约定（Phase 47 对齐后的口径）。

### 4.2 宿主 handler/schema（l3_singlecell.py）

- sc_pseudotime handler 签名加 `dyn_top_n: int = 50`、
  `root_cluster: str = ""`，payload 透传；timeout_sec=1200 沿用
  （19149 细胞 DPT 现状已验，Spearman 全 HVG ~2000 基因为
  O(基因×n log n) 秒级）。
- schema description 更新 Phase 32/53；properties 加两参数。

### 4.3 镜像

零 pip 变更零新依赖（scipy/statsmodels 随 scanpy 现成）；COPY
sc_tools/ 层重建即可。

## 5. 测试设计

### 5.1 单测（tests/unit/test_l3_singlecell.py，+2）

- 透传断言：dyn_top_n/root_cluster 进 payload；schema 无 enum
  （数值+字符串），断言默认 payload dyn_top_n=50、root_cluster=""。

### 5.2 冒烟（新建 scripts/_smoke_sc_pseudotime.py，sc 侧第二个）

宿主 scanpy 造 processed 壳（复用 _smoke_st_stats.py 宿主建库
模式，无需容器内建库——pseudotime 不依赖资源库）：

- 合成 300 细胞 1D 分化梯度：t=linspace(0,1,300)，基因 G_up
  表达∝t、G_down∝(1-t)，其余 198 基因随机；scanpy 全链
  （normalize/log1p/HVG/neighbors/UMAP/leiden/raw）。
- 断言①默认调用：G_up/G_down 进 top_dyn（Spearman 应 ≈±1）且
  三产物落盘；②root_cluster=众数簇：ok 且 root_mode=cluster、
  root_cell_index 属于该簇；③root_marker+root_cluster 同给 →
  INVALID_INPUT；④dyn_top_n=0 → ok 且无 dyn 段（现状兼容）。

### 5.3 真机验收（b7a44c75464f，19149 细胞）

- 默认（root_marker 空）+ dyn_top_n=50：耗时/maxrss 实测钉注；
  top_dyn 生物学合理性（免疫图谱分化轴基因）。
- root_cluster=<最大 T 细胞簇>：定根后 per_cluster 伪时序均值
  梯度方向合理（root 簇均值最小）。

## 6. 风险与纪律

- **探针先行**：冒烟前容器探针核实 statsmodels 在镜像可用、
  spearmanr 对常数表达基因（零方差）返回 NaN 的处理（rho=NaN
  需掩掉不进入排序）。
- **SearchReplace 假成功纪律**：批量编辑后逐文件逐处 Grep 验证。
- **容器脚本改动必重建镜像**（COPY sc_tools/ 层）。
- 冒烟/真机断网容器口径：`--network none --cpus 4 --memory 8g`。
