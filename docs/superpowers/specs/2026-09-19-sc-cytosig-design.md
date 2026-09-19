# sc_cytosig 细胞因子信号预测设计（Phase 71）

## §1 定位与形态

现状缺口（2026-09-19 探针 probe_sc_cytosig 三判据 PASS 后立项）：

- 全树无细胞因子信号预测工具；与 nichenet（配体活性）、cellchat
  （配受体通讯）正交的第三维：**细胞因子响应谱**（figS7E 式）；
- CytoSig pip 包（0.0.3）核心 `ridge_significance_test` 硬依赖
  `data_significance`（2021 年 GSL C 扩展），现代环境安装坎坷——
  探针三轮钉死终解：**镜像内独立 venv `/opt/cytosig_env`**
  （numpy<2 与 CytoSig 同命令约束防安装期升级 + apt
  libgsl-dev build-essential），主环境零污染；
- 签名 4881 基因 × 43 因子（TGFB1 top 基因 GADD45B/JUNB/FSTL3 与
  文献吻合，签名自带 sanity）。

落地：**新工具 sc_cytosig**（sc 第 30 工具），跨运行时桥架构——
容器脚本 `sandbox/sc_tools/cytosig.py` 跑在 bio 主 python（anndata/
matplotlib 生态），ridge 计算经 tsv 落盘 + subprocess 调
`/opt/cytosig_env/bin/python /opt/sc_tools/_cytosig_core.py`（nichenetr
R 桥同款哲学：异构运行时以文件为界）。

## §2 数据流

```
dataset_ref（processed.h5ad）+ groupby 列 + mode
  ├─ 表达矩阵选择：adata.raw 有则 raw.to_adata()（log 全基因快照，
  │   签名交集最大化），否则 X；var_names 与签名 index 的大小写经
  │   upper 对齐（common.upper_gene_map 桥）
  ├─ 交集护栏：|签名基因 ∩ var| < 500 → CYTOSIG_LOW_OVERLAP 拒收
  ├─ mode=per_cell（逐细胞）：
  │   基因×细胞矩阵 → venv ridge（nrand 置换）→ beta/zscore/pvalue
  │   （因子×细胞）→ 按 groupby 各群 beta.mean + zscore.mean 汇总列
  ├─ mode=diff（差分谱，论文推荐口径）：
  │   逐群（groupby 值域）差分向量 d = 群均值 - 其余均值（基因×1）
  │   → 拼成 基因×群 矩阵 → venv ridge → 因子×群
  ├─ 产物三件（见 §6）
  └─ emit（见 §7）
```

关键决策记录：

1. **diff 口径用均值差而非 DE 检验统计量**：CytoSig README 差分谱
   即表达差形态；scanpy rank_genes_groups 的 logFC 也是均值差变体，
   直接算免去依赖与多重校正语义混入。
2. **per_cell 与 diff 双模式都做**：per_cell 保群内异质性面
   （zscore.mean 汇总即技能口径 A），diff 是论文展示口径（口径 B）；
   默认 diff。
3. **venv 桥以 tsv 为界**：主环境写 基因×样本 tsv + 读回 因子×样本
   结果 tsv——numpy/pandas 双端兼容，无 pickle 版本风险。
4. **nrand 暴露参数**（默认 1000，CI/快速验证可降 200）：置换次数
   直接控精度与耗时。
5. **beta 与 zscore 双口径并列输出**（技能坑 2）：beta=信号强度
   （CytoSig 网站排名口径）、zscore=置换显著性——产物与 emit 摘要
   都双列，note 钉注不可混用。

## §3 stdin 契约

```json
{"dataset_ref": "abc123", "groupby": "celltype", "mode": "diff",
 "nrand": 1000}
```

仅 `dataset_ref` 与 `groupby` 必填。

## §4 参数（ToolSpec / handler 一致）

| 参数 | 必填 | 默认 | 约束 |
|---|---|---|---|
| dataset_ref | ✓ | — | 需 processed.h5ad |
| groupby | ✓ | — | obs 分组列（diff 分群 / per_cell 汇总） |
| mode | | diff | diff/per_cell |
| nrand | | 1000 | 100..5000 |

risk_level="L1_compute"；timeout_sec=1800（探针 20 样本秒级，
1000 细胞一次调用 C 实现，宽裕帽；模块常量 `_SC_CYTOSIG_TIMEOUT`）。

## §5 产物（落 `/ws/{ref}_cytosig/`）

- `cytosig_scores.csv`：因子 × 样本（diff=群，per_cell=细胞）的
  beta/zscore/pvalue 三段宽表（rank 内嵌）
- `cytosig_heatmap.png`：beta 矩阵热图（top20 因子 × 全样本，
  按最大 beta 排序）
- `cytosig_top.png`：逐样本 top5 因子条形（beta 实心 + zscore 参考）

## §6 emit 结构

`{ok, dataset_ref, mode, groupby, n_factors: 43, n_genes_used,
n_samples, top_by_beta: {样本: [(因子, beta)...top5]},
top_by_zscore: {样本: [...]}, heatmap_png, scores_csv, note}`——
note 含双口径钉注与 raw/X 矩阵来源说明。

## §7 错误码

| 码 | 条件 |
|---|---|
| INVALID_INPUT | mode 非法；groupby 不在 obs；groupby 单值 |
| CYTOSIG_LOW_OVERLAP | 签名基因交集 <500 |
| CYTOSIG_VENV_MISSING | /opt/cytosig_env 不存在（提示 rebuild bio image） |
| CYTOSIG_CORE_FAILED | venv 内核非零退出（stderr 尾随） |

## §8 bio.Dockerfile 层（harmonypy 层后插入）

```dockerfile
# Phase 71 细胞因子信号：CytoSig ridge + data_significance（GSL C
# 扩展，numpy 1.x 编译期硬约束）→ 独立 venv，主环境零污染。numpy<2
# 与 CytoSig 同命令约束：--no-build-isolation 只管构建期，安装期 pip
# 仍会把 venv numpy 升到 2.x（C 扩展编译于 1.x，运行时 ABI 崩）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgsl-dev build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/cytosig_env \
    && /opt/cytosig_env/bin/pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "numpy<2" pandas setuptools wheel \
    && /opt/cytosig_env/bin/pip install --no-cache-dir --no-build-isolation \
        -i https://pypi.tuna.tsinghua.edu.cn/simple "numpy<2" CytoSig \
    && /opt/cytosig_env/bin/python -c "import CytoSig, numpy; \
        print('cytosig venv ok, numpy', numpy.__version__)"
```

pip 清华源自带重试（无 zenodo 类裸连大文件），无需 dl()。

## §9 L3 注册与连带清单

- `l3_singlecell.py`：handler `sc_cytosig`（runner.run("cytosig",
  {...})，无文件挂载）+ ToolSpec 注册（sc_tcr 之后）
- `orchestrator/report/section_digest.py` SECTION_TITLES：
  `"sc_cytosig": "细胞因子信号预测（CytoSig）"`
- 附录 A 口径表：`sc_cytosig` 1800 行（表守护测试强制）
- `tests/unit/test_l3_sc_cytosig.py`：注册/dispatch/默认值/错误
  传递 4-5 用例
- `ci.yml`：bio-image-smoke 追加 `_smoke_sc_cytosig` 步

## §10 测试

1. **契约**：注册 schema（mode enum/nrand 范围/timeout 1800）+
   参数透传 + BioRunError 传递
2. **冒烟 `scripts/_smoke_sc_cytosig.py`**（探针地面真值复用）：
   合成 h5ad（签名基因子集 800 + 噪声基因 200，A 群注入
   2.0×sig[TGFB1]）→ 场景：①diff 模式 TGFB1 beta rank≤3 且断层
   ≥2×第二名；②per_cell 模式 A 群 TGFB1 汇总 beta 显著 > B 群；
   ③噪声 B 群 p<0.01 因子 ≤2/43；④mode 非法 INVALID_INPUT；
   ⑤groupby 缺列 INVALID_INPUT；⑥全假基因 CYTOSIG_LOW_OVERLAP
3. **镜像内端到端**：重建 bio 镜像（venv 层真跑）→ 冒烟全绿
4. **真机挂账**：oscc/599sub 任一真机集跑一轮看因子谱合理性
   （TGFB1/IFNG 等免疫因子在免疫 niche 高）——不阻塞收官

## 非目标

- 不做 12 组合稳健性矩阵（marker 重选×输入×签名，作者对照口径）
  ——挂账远期，需要时按技能 §稳健性 加
- 不做 expand_signature 扩展签名（默认 43 因子高置信度口径）
- 不暴露 alpha/cnt_thres/flag_normalize（技能钉默认 alpha=1e4、
  cnt_thres=10 别改）
- 不做逐细胞写回 obs（43 因子列膨胀；_scores.csv 群级已够下游）
