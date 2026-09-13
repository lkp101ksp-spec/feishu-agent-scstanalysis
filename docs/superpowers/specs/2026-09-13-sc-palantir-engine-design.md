# sc_pseudotime 第三引擎：Palantir（设计，2026-09-13）

## §1 背景与目标

Phase 32 交付 diffmap+DPT 拟时序基座，Phase 53 加动态基因趋势与簇定根。
Palantir（Setty 2019, Nat Biotechnol）是扩散图族的另一主流引擎：以
start_cell 为起点做马尔可夫链随机游走，给出伪时序 + **终末态 + 分支
概率**——补齐 DPT"只排序不分支"的语义短板（分支推断挂账项的部分解）。

依赖探针已收官（两轮，_eval/probe_deps.py + palantir_trial.py 留证）：
- bio cpu 镜像（py3.12.14 + numpy 2.5.2）直装 `palantir==1.4.5`
  **零 pin 冲突**（mellon 1.7.1 已修 numpy 2 兼容）；
- 闭包 jax/jaxlib 0.11.1 + jaxopt 0.8.5 + ml_dtypes 0.6.0 + igraph
  1.0.0，约 500MB，零 torch/tensorflow、零编译、零运行时下载
  （--network none 全流程跑通）；
- 300 细胞合成梯度全工作流伪时序 Spearman rho=0.9917。

目标：sc_pseudotime 加 `engine="palantir"`，复用既有定根体系，产出
伪时序 + 终末态，分支概率矩阵落 csv 不画图（YAGNI）。

## §2 集成形态（循 cnv.py 双后端先例）

新引擎**不开新工具**——sc_pseudotime 加参数（与 infercnvpy+cnvturbo
双后端同构，工具面不膨胀）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `engine` | `"dpt"` | `"dpt"`（现行为）/ `"palantir"`；非法值 fail INVALID_INPUT |
| `start_cell` | `""` | 显式根细胞条码（仅 palantir 引擎）；优先级高于 root_marker/root_cluster；条码不存在 fail INVALID_INPUT |

stdin 新增两键，其余不动。`engine="dpt"` 时行为与 Phase 53 完全一致
（回归零变化）。

## §3 pseudotime.py 分支设计

main 在定根后按 engine 分路：

- **DPT 路**（现逻辑原样）：diffmap → dpt → 三产物 + dyn。
- **Palantir 路**：
  1. 前置校验：PCA（`X_pca`）+ neighbors 缺一 → fail INVALID_INPUT
     （与现约定一致；leiden/X_umap 校验已在公共段）；
  2. start_cell 解析三优先：显式 `start_cell` 条码 > root_cluster 度根
     > root_marker 最高表达 > cell#0（复用 `_degree_root`/`_raw_expr`，
     root_mode emit 加 `"explicit"` 第四种）；
  3. `palantir.utils.run_diffusion_maps(n_components=5)` →
     `determine_multiscale_space()` → `core.run_palantir(start_cell,
     num_waypoints=1200)`（官方默认；真机打点后再调）；
  4. 产物（写 `<ds>/pseudotime/` 目录，文件名加 palantir 前缀不与
     DPT 产物互踩）：
     - `palantir_pt.csv`：barcode, leiden, palantir_pseudotime,
       各终末态分支概率列（branch_probs 宽表并入，一文件齐）；
     - `terminal_states.csv`：终末态条码 + 所属 leiden + pt；
     - `palantir_umap.png`：pt viridis 着色 + 根红圈 + 终末态黑叉；
  5. emit：`method="palantir"`、root_* 同构、`n_terminal`、
     `terminal_states`（前 5 条码+簇）、三产物路径、
     `per_cluster` pt 均值表（与 DPT 同构便于对照）。
- **dyn 动态基因**：Palantir 路同样支持（dyn_genes 复用，pt 换
  palantir 伪时序）——`_dyn_genes` 无 DPT 耦合，直接传入即可；
  产物名沿用 dyn_genes.csv 等（同目录分引擎文件名不冲突：dyn_* 
  两引擎同名会互踩 → palantir 路产物名加前缀 `palantir_dyn_*`）。

## §4 镜像层（bio.Dockerfile）

新层（循 muon/liana 层先例，独立层保缓存）：

```dockerfile
# Palantir 拟时序引擎（探针钉注：py3.12+numpy 2.5.2 零 pin 冲突，
# 闭包 jax/jaxopt/ml_dtypes/igraph ~500MB 零编译，断网可用）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple palantir==1.4.5 \
    && python -c "import palantir; print('palantir ok')"
```

构建：`docker build -t feishu-research-agent/bio:cpu-latest sandbox
-f sandbox/bio.Dockerfile`，构建后容器内跑 palantir_trial.py 冒烟
（rho>0.9）再进 TDD 后续步骤。

## §5 错误处理（对齐现有 fail INVALID_INPUT 约定）

| 场景 | 行为 |
|---|---|
| engine 非法值 | fail INVALID_INPUT（列出合法值） |
| start_cell 非空且不在 obs_names | fail INVALID_INPUT（不兜底，显式参数须严格） |
| start_cell 与 root_marker/root_cluster 同给 | 允许——start_cell 优先，emit note 说明 |
| engine=palantir 但缺 X_pca/neighbors | fail INVALID_INPUT |
| palantir import 失败（镜像未重建） | run() 兜底 SCRIPT_ERROR（既有约定） |

## §6 测试策略（TDD 红→绿）

1. **单测**（tests/unit/test_l3_singlecell.py，+2 用例）：
   engine/start_cell 透传 runner.run + schema 含两参数；默认值
   engine="dpt"/start_cell=""（回归零变化断言）。
2. **冒烟**（scripts/_smoke_sc_pseudotime.py 加 palantir 场景）：
   宿主建库（Phase 53 同款替换式注入合成库）→ 容器跑
   engine=palantir：①pt vs 真值 t 的 |rho|>0.9；②n_terminal≥1；
   ③root_cluster 定根 root_mode=cluster；④start_cell 显式条码
   root_mode=explicit；⑤非法条码 fail INVALID_INPUT。
3. **真机 19149 双引擎对照**：root_cluster=2（Phase 53 同款 T 簇）：
   Palantir pt vs DPT pt Spearman 一致性（预期强正相关）；终末态
   落在非根簇；dyn top 基因与 Phase 53 T 身份基因重叠度抽查；
   耗时/RSS 打点（jax 首 import 编译缓存开销钉注头注释）。

## §7 风险与钉注

- jax 首次 import XLA 编译缓存：真机耗时打点，超 timeout 1200 才调；
- 内存：palantir 在 multiscale/diffusion 上为稠密矩阵运算，19149 量级
  预估 <4GB（真机 probe 验证后钉注）；
- num_waypoints=1200 官方默认，19149 细胞足够；59900 量级若超时再调；
- 分支概率列数=终末态数（动态），csv 宽表列名 ts_<barcode>。

## §8 范围外（挂账）

- 分支概率可视化（等用户需求）；CytoTRACE2（探针不推荐）；
  RNA velocity（数据前提不满足，等含剪接层数据）；
  Palantir MAGIC 插补（非本工具职责）。
