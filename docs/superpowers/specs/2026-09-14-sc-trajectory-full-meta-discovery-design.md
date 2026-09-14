# Phase 59 设计：trajectory_full 全景模式 + obs 分组列发现 + 门禁取证

日期：2026-09-14｜状态：已批准（用户"好的，按这个方案来"）

## §0 纯评估三项（零产品代码，本 Phase 内交付）

- ①双图谱轨迹叙事整合报告（markdown 交付 _eval 不落库）：
  三引擎一致性 + 谱系聚合 + 命运决定基因 + 支间通讯收口；
- ②19149 三引擎 spearman 矩阵（root_cluster=2 天然锚定，
  obs 已有 palantir/slingshot/paga_dpt 三列，一次容器跑）；
- ③dyn_modules × lineage_branch 二次挖掘（模块表达波在
  命运支间差异，_eval 脚本）。

## §1 sc_pseudotime trajectory_full（pseudotime.py + l3）

- 新参数 trajectory_full（bool 默认 False）：true 时忽略 engine，
  内部串跑 palantir 相（pt + branch_top_n 默认 50 可覆写）
  → slingshot 相 → cross 自动触发 → paga/paga_pt 跟随 flags；
  root_cluster/start_cell 两相共用；dyn 相跟随 dyn_top_n 双相各跑。
- emit 合并两相键 + trajectory_full: true + engine_ignored note；
  l3 handler 透传 + schema property；TDD +2（透传+默认 False）。
- 59900 全链预估 ~12min < timeout 3600s 安全。

## §2 obs 分组列发现（meta.py）

- sc_meta emit 扩 group_cols：obs 类别列（2≤nunique≤100）全列
  + trajectory_cols（palantir_branch/lineage_branch/
  slingshot_lineage/*_pseudotime 模式）单列标注；零新参数、
  l3 零变更；真机双图谱验证。

## §3 check.ps1 抖动取证

- pytest 层输出落 .check_pytest.log：FAIL 保留 + echo 尾部 50 行，
  PASS 删除。

## §4 测试与门禁

- TDD +2 → 全量回归 1299→**1301**；
- 冒烟 pseudotime 加场景⑳（trajectory_full 全相跑通 + engine
  忽略 note + 双引擎 obs 列齐备）；
- 真机：19149 trajectory_full 全链 + sc_meta 双图谱 group_cols。

## §5 范围外

RNA velocity/GCM PAT/CytoTRACE2 挂账维持；cellchat diff 命运支
模式；sc_pseudotime 多数据集批量。
