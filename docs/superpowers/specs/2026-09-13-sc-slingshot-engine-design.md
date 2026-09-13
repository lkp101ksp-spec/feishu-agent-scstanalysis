# sc_pseudotime 第四引擎 Slingshot 设计（2026-09-13）

## 0. 背景与探针结论

Slingshot（Street 2018）= 簇级 MST 谱系 + 主曲线拟合，分叉轨迹公认强项，
补 DPT（单链）/Palantir（概率分支）之外的第三视角：**显式谱系条数与曲线几何**。

探针四轮钉注（测试总结第三十五段）：

- 镜像已有 R 4.5.0（Phase 37 层）；g++/r-base-dev 构建期临时装、装完 purge
  （bbknn/scrublet/scTenifoldKnk 三先例）；**libxml2/libglpk40 为运行期常驻库**
  （预装 igraph.so 的 ldd 缺口，不 purge）。
- 装包三源：CRAN=清华 + Bioc=bioconductor.org/3.21/bioc + data/annotation
  （清华/USTC 仅托管当前 3.23 分支，R 4.5 配对仓唯官方 3.21；
  GenomeInfoDbData 在独立 annotation 仓）。
- 实测：INSTALL_MIN=3.4、CLOSURE_N=74、LIB_DELTA_MB=67、LOAD_OK=2.16.0；
  运行期零下载，验证容器 `--network none` 不受影响。

## 1. 集成形态（用户批准：并入 engine）

- `engine` enum `dpt`（默认）/ `palantir` / `slingshot` 三引擎并列，
  循 Phase 54 palantir 先例（schema enum 扩一值，默认零行为变化）。
- 下游相零改动复用：`dyn_top_n` / `dyn_modules_k` / `modules_enrich`
  基于 slingshot 主 pt 照常可跑（`_dyn_genes` 只消费 pt 向量）。
- `start_cell` 校验放宽：原仅 palantir 合法 → palantir/slingshot 均合法；
  `branch_top_n` 维持 palantir 专属（slingshot+branch>0 → INVALID_INPUT）。

## 2. 定根映射（复用 main 定根四模式，零新增分支）

main 现有四模式产出 `iroot` 不变；slingshot 支路追加映射 `start.clus`：

| root_mode | start.clus |
|---|---|
| explicit（start_cell）/ cluster（root_cluster）/ marker | `clusters[iroot]`（根细胞所在簇标签） |
| fallback | 不传（slingshot 自由推根），root_note 注明 |

## 3. 桥接契约（循 knockout.py / knk.R 先例）

Python 侧 `_run_slingshot(...)`（pseudotime.py）：

1. 导出 `ds_dir/_sling_in/`：`reduced.csv`（cell, UMAP1, UMAP2——
   **X_umap 两维**，用户批准；曲线与可视化同坐标系）、
   `clusters.csv`（cell, leiden）。
2. `subprocess.run(["Rscript", "/opt/r_tools/slingshot_bridge.R",
   reduced, clusters, out_dir, start_clus], timeout=3300,
   capture_output=True)`；非零退出 → RuntimeError 带 R stderr 尾 1500 字符
   （knockout 同式）。
3. 缺产物哨兵：out_dir/sling_pst.csv 不存在 → RuntimeError 带 stdout 尾段。

R 侧 `sandbox/r_tools/slingshot_bridge.R`（COPY r_tools/ 惯例自动入镜像）：

- argv：reduced_csv clusters_csv out_dir start_clus（空串=自由推根）。
- `getLineages(reduced, clusters, start.clus=?)` → `getCurves(...)`；
- 产物：`sling_pst.csv`（cell × lineage1..k 的 slingPseudotime 宽表，
  NA=细胞不在该谱系）、`sling_curves.csv`（lineage, ord, UMAP1, UMAP2
  曲线折点，供 Python 侧叠图）；stdout 打印 `SLING_DONE <n_lineages>`。

## 4. Python 读回与产物

- 主 pt = 每细胞所属谱系 pt 的行均值（NA 忽略）；全 NA 细胞 → nan。
- `adata.obs["slingshot_pseudotime"] = pt` + `adata.write_h5ad(processed)`
  （palantir 分支写回惯例，下游 sc_plot 可着色）。
- 产物三件套：
  - `slingshot_pt.csv`（cell, leiden, lineage1..k, slingshot_pseudotime）
  - `slingshot_curves.csv`（R 侧产物透传/复核）
  - `slingshot_umap.png`（UMAP 底图按主 pt viridis 着色 + 每谱系曲线
    tab10 叠加 + 根细胞红圈，dpt umap 图惯例）
- emit：`method="slingshot"`、`n_lineages`、`slingshot_pt_csv` /
  `slingshot_curves_csv` / `umap_png`、`per_cluster`（_cluster_stats 复用）、
  dyn 相产物照旧展开。

## 5. 镜像层（bio.Dockerfile，追加于 palantir 层后）

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       r-base-dev g++ libxml2 libglpk40 \
    && Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/', Bioc='https://bioconductor.org/packages/3.21/bioc', BiocAnn='https://bioconductor.org/packages/3.21/data/annotation'), timeout=600); install.packages('slingshot', Ncpus=4)" \
    && Rscript -e "library(slingshot); cat('slingshot', as.character(packageVersion('slingshot')), 'ok\n')" \
    && apt-get purge -y --no-install-recommends r-base-dev g++ \
    && rm -rf /var/lib/apt/lists/*
```

- libxml2/libglpk40 为运行期库常驻不 purge（igraph.so ldd 缺口探针钉注）。
- 探针配方逐字（含 LOAD_OK 自检内嵌构建层，装不上即 build 失败）。

## 6. 校验

- `engine` 非三值之一 → INVALID_INPUT（报错文案列三值）。
- engine=slingshot 缺 X_umap/leiden → INVALID_INPUT（现有公共校验已覆盖）。
- engine=slingshot + branch_top_n>0 → INVALID_INPUT。
- start_cell 校验放宽为 palantir/slingshot 双合法。

## 7. 测试

- TDD +2（test_l3_singlecell.py，循 palantir 双例）：透传
  （engine=slingshot+root_cluster 进 payload）+ 默认值零变化
  （engine 默认 dpt 不动）。
- 冒烟 +2（_smoke_sc_pseudotime.py，13→15 场景）：
  - 场景⑭：合成主干+双分叉库（trunk 100 + A/B 各 100，注入式表达，
    cellfreq 冒烟合成惯例），engine=slingshot+root_cluster=主干簇+dyn20
    → n_lineages≥2、主 pt 与真值 Spearman≥0.8、三产物落盘、
    obs 重读 slingshot_pseudotime 存在；
  - 场景⑮：engine=slingshot+branch_top_n=10 → INVALID_INPUT。
- 真机双图谱：19149（root_cluster=2）+ 59900（fallback 定根），
  与 palantir pt 交叉 Spearman 对照（读 obs 双列）。
- 全量回归预期 **1294**（1292+2）。

## 8. 范围外（YAGNI）

- X_pca 输入对照（用户已选 X_umap 单路）。
- 谱系权重/分支概率分析（palantir 已覆盖概率视角）。
- slingshot 高级参数暴露（thresh/shrink/approx_points 等，首轮全默认）。
- getLineages 的 end.clus 约束。
