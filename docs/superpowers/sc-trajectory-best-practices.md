# sc 轨迹分析最佳实践（Phase 53–59 沉淀）

适用：sc_pseudotime / sc_plot / sc_cellfreq / sc_cellchat / sc_meta。
数据前提：processed.h5ad 含 X_pca、X_umap、leiden。

## 1. 标准调用序列（一键全景 + 消费链）

```
① sc_pseudotime  trajectory_full=true, root_cluster=<锚定簇>,
                 dyn_modules_k=6, modules_enrich=true, paga=true, paga_pt=true
② sc_meta        op=list_cols                      # 只读，发现分组/轨迹列
③ sc_plot        kind=umap_obs, obs_cols=[lineage_branch, slingshot_lineage, ...]
④ sc_cellfreq    celltype_col=lineage_branch, group=<分组列>   # 命运偏向
⑤ sc_cellchat    celltype_col=lineage_branch       # 支间通讯
```

trajectory_full 一次产出：palantir 相（分支推断+命运 DE）→ slingshot 相（谱系）
→ cross 自动（triggered_by="trajectory_full"）→ PAGA/PAGA-DPT 跟随。
emit 嵌套 palantir/slingshot 两 dict；与分步链产物等价（19149 已逐字验证）。

## 2. 根锚定纪律（最高优先级）

- **必须显式给 root_cluster**（生物学已知祖/干细胞簇）。fallback 根（cell #0）
  常落谱系尖端 → slingshot 单谱系退化（n_lineages=1、n_cross_sig=0）。
  合成双分支数据须 trunk 锚定（Phase 55/59 两次同族教训）。
- **跨引擎相关性比较必须同 iroot**。未锚定时方向任意：59900 pal↔paga 曾
  -0.2453，锚定 cluster 31 后 +0.8075。锚定后双图谱三引擎两两全正
  （19149: 0.8934/0.6539/0.6483；59900: 0.7183/0.8075/0.5785）。
- palantir 终末态以 cell barcode 命名——**支名即样本前缀时警惕支≈病人**（见 §4）。

## 3. 产物与 obs 写回列

| obs 列 | 语义 | 写回时机 |
|---|---|---|
| palantir_branch | palantir 终末态归属（max_prob<0.6 → unassigned） | branch 相 |
| palantir_pseudotime / slingshot_pseudotime / dpt_pseudotime / paga_dpt_pseudotime | 各引擎拟时 | 各相 |
| slingshot_lineage | argmax 谱系归属（把 palantir unassigned 兜住，覆盖 100%） | cross 相 |
| lineage_branch | 谱系→主导命运支物化（frac<0.5 → mixed） | cross 相 |

判定信号：n_terminal<2 → 无 branch_dyn/DE（单终末无分支）；lineage frac<0.5
→ mixed 正确降级，勿强行归支。

## 4. 解读陷阱

- **支与分组共线检查**：先跑 branch × batch/sample/group 列联。19149 实测
  batch=sample=group 三列冗余、支 A 96.9%/支 B 90.1% 归属各自样本——
  "双命运支"实为病人间异质性，fate_bias OR=281 是样本效应而非体内双命运。
- CNV 亚克隆三角：整克隆单边（16/16）提示克隆级命运倾向，但与病人共线时
  "内在 vs 微环境"不可解耦，如实钉注。
- 模块身份判定用 marker 交叉而非凭直觉：M6 疑似浆细胞，实为滤泡/成熟 B
  （Ighd/Ighm/Ms4a1/Pax5，无 Xbp1/Jchain/Sdc1）。
- 动态基因 rho 方向结合身份读：T 身份基因（TRAC/CD247）沿拟时下调=程序退场，
  非去分化。

## 5. 常见失败与修复

| 症状 | 根因 | 修复 |
|---|---|---|
| slingshot n_lineages=1、cross sig=0 | 根落尖端 | root_cluster 锚定祖簇 |
| branch 相无 branch_dyn_csv | n_terminal=1 | 数据单分支属正常，换图谱或接受 |
| 跨引擎 spearman 为负 | 未锚定/不同根 | 同一 root_cluster 重跑双引擎 |
| 支间差异 OR 极大 | 支≈样本共线 | 先查 §4 共线列联再解读 |

## 6. 验收断言模板（trajectory_full）

```python
assert out["method"] == "trajectory_full"
assert out["palantir"]["n_terminal"] >= 2          # 分支结构存在
assert "branch_dyn_csv" in out["palantir"]         # 命运 DE 产物
assert out["slingshot"]["n_lineages"] >= 2         # 多谱系
assert out["slingshot"]["n_cross_sig"] >= 1        # 交叉显著
assert out["slingshot"]["cross_triggered_by"] == "trajectory_full"
```

## 7. 资源参考（真机实测）

19149（19149 细胞）全景一次 ~2m40s；59900（59900 细胞）palantir 相 3-4m；
cellchat lineage_branch 35m（59900）/13m（19149）——工具 timeout 3600s 够用。
