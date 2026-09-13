# Palantir 分支概率可视化（设计增补，2026-09-13）

增补自 2026-09-13-sc-palantir-engine-design.md（Phase 54 §3 挂账项
"分支概率可视化"落地，方案经用户"继续"批准）。

## §1 目标

`_run_palantir` 现有三产物（pt 宽表/终末态表/UMAP pt 图）之上补第
四产物：分支概率 UMAP 分面图，回答"哪群细胞倾向哪个终末命运"。

## §2 形态（方案 A，三选一批准）

UMAP 分面图 `palantir_branch_umap.png`：
- 1×N 横排 panel（N=终末态数，封顶 6；N>6 取全细胞最大概率 top6
  并在 emit 加 `branch_note` 说明截断）；
- 每 panel：全部细胞按该终末态分支概率着色（viridis，
  **vmin=0/vmax=1 固定色阶**保证跨 panel 可比），终末态细胞黑叉、
  根细胞红圈；
- panel 标题 `ts_<条码尾8位> (leiden <簇>)`；N=1 单 panel 不塌缩
  （figsize 与 palantir_umap 一致）。

## §3 实现

- `_run_palantir` 内追加 ~25 行（复用已算的 terms/tidx/branch
  矩阵），零新依赖、零新 stdin 参数（YAGNI：不出堆叠/热图）；
- emit 加 `branch_umap_png` 键（+ 截断时 `branch_note`）；
- 头注释产物清单同步更新。

## §4 验证

- 冒烟（_smoke_sc_pseudotime.py 场景⑤追加断言）：文件落盘 +
  n_terminal≥1 时 panel 数=N（读图不便，改为断言 emit 键存在
  + 文件非空 + 19149 真机人工目检）；
- 真机 19149 复跑（root_cluster=2，2 终末态=2 panel）出图目检；
- 镜像纪律（Phase 54 教训）：脚本变更后重建镜像再验；
- 回归基线 1288 不变（无新单测——sandbox 脚本靠冒烟覆盖，
  emit 键由冒烟断言）。
