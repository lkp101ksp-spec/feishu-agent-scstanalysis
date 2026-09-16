# bio_trajectory_pipeline skill 设计（Phase 60 / 建议③）

日期：2026-09-15｜承接：Phase 53-59 轨迹全链 + sc-trajectory-best-practices.md（§1-§7）

## 0. 动机与消费者定位

- skills/ 体系由 coding agent 消费（SkillLoader 热加载，gateway/runtime 注入）；
  bio 主链（research runner→L3 sc_*）不读 skills。
- ③ 的目标是把五次 Phase 打磨的轨迹能力"送出去"：用户在 coding agent 一句话
  （"给这个数据集做轨迹分析"）即按最佳实践编排执行，纪律内建（root_cluster
  锚定、解读陷阱），无需懂行。
- 分析逻辑零重复：编排脚本 subprocess 调 docker bio 镜像内 /opt/sc_tools/*
  （与 L3 同一份代码）。

## 1. 交付物

- skills/bio_trajectory_pipeline/SKILL.md：知识面——触发词（轨迹/拟时序/
  分化/trajectory/pseudotime/命运）、调用前提（processed.h5ad 含 X_pca/
  X_umap/leiden；root_cluster 必填，须由用户提供或按 marker 推断祖细胞簇
  并向用户确认）、产物路径与解读陷阱（支×分组共线检查、mixed 降级、
  单谱系退化→root 锚定）。
- skills/bio_trajectory_pipeline/tools.yaml：工具 bio_trajectory_pipeline
  （dataset_id*、root_cluster*、species=human|mouse、steps=full|fast、
  group_col 可选）；timeout 7200（full 含 cellchat，59900 实测 35min）。
- skills/bio_trajectory_pipeline/run_pipeline.py：编排脚本——
  1. pseudotime trajectory_full（dyn_modules_k=6、modules_enrich、paga、paga_pt）
  2. meta list_cols（分组列发现，供 3/4/5 选列）
  3. plot umap_obs（lineage_branch/slingshot_lineage）
  4. cellfreq celltype_col=lineage_branch（group 列存在时）
  5. steps=full 时 cellchat celltype_col=lineage_branch（按 species 选库）
  各步 stdin JSON→docker→emit 末行 JSON；汇总写
  bio_workspace/<ds>/trajectory_pipeline_summary.json
  （各步 ok/关键指标：n_lineages、n_cross_sig、fate_bias top、产物路径）。

## 2. 错误与边界

- 缺 root_cluster：直接拒绝（INVALID_INPUT 语义，脚本内显式报错+提示
  如何选簇——引用 SKILL.md 纪律，不 fallback）。
- processed.h5ad 缺 X_pca 等：透传工具层 INVALID_INPUT。
- docker/镜像缺失：首步失败即中止并报明确指引。
- obs 列不存在（如 slingshot_lineage 未写回）：该列跳过并在 summary 标注。

## 3. 测试

- 单测 +2（tests/unit/test_bio_skill_trajectory.py）：SkillLoader 能加载
  新 skill（name/description/工具参数 schema 断言）；tools.yaml 参数
  required 集合断言。
- 冒烟：真机 19149 steps=fast（root_cluster=2，复现已验证链，~3min），
  summary 各步 ok、指标与 Phase 59 全景一致（n_lineages=12、sig=11）。

## 4. 范围外

- research/bio 主链的 skill 注入（需改 bio runner prompt 面，另立 Phase）；
- cellchat 资源分级（建议⑥，另行）；报告闭环（建议④，下一步）。
