# 执行面统一设计（Spec）

- 日期：2026-09-17
- 状态：全项落地（核心项 + 挂账①②③均收官，附录 A 口径表测试守护）
- 来源：遗留建议④（"执行面统一（需立 spec）"）+ 执行面现状调研（2026-09-17）

## 1. 问题定义

"执行面"= 工具从 ToolSpec 声明到容器真实执行的分发配置面
（image / script_dir / timeout / docker 参数）。现状三处漂移：

1. **超时漂移（真 bug，已修）**：st_load/st_qc/st_process/st_markers/
   st_plot/st_domains/st_commot 七工具 handler 未传 timeout_sec，
   容器静默回退 BioRunner 默认 900s——其中 st_process(1800)/
   st_markers(1200)/st_domains(1200)/st_commot(1800) 声明值高于
   实际值，真实数据会被容器提前杀（ToolSpec 纸面超时不可信）。
2. **skill 侧绕过治理面（已修）**：bio_trajectory_pipeline/
   run_pipeline.py 硬编码镜像 tag、无 --name 僵尸容器兜底——
   settings 换 tag 即漂移。
3. **声明与消费分离（保留现状+合同测试钉死）**：
   ToolSpec.timeout_sec 无执行层消费点（纯声明/文档字段），
   真正生效的是 handler 手写的 runner.run(timeout_sec=...)——
   两处独立维护必然漂移。

## 2. 决策

### D1 超时一致性 = 合同测试（非自动消费）[已落地]

方案取舍：
- （否决）注册包装器自动从 spec 注入 timeout——handler 签名/闭包
  结构改动大，且 st_deconvolve 参数化超时（settings 注入）无法
  静态绑定；
- （选定）**合同测试**：tests/unit/test_l3_dispatch_contract.py
  以 FakeRunner 逐工具调用 handler（参数按 ToolSpec.parameters
  自动合成，新增工具零改动入列），断言 run(timeout_sec) ==
  spec.timeout_sec 且恰好一次调用。漂移在 CI 即红。

纪律钉入两 l3 模块头注释：新增工具必须双写且过合同测试。

### D2 st 七工具超时补齐 [已落地]

handler 显式传值与 spec 对齐（600/600/1800/1200/600/1200/1800）。

### D3 skill 编排镜像治理 [已落地]

run_pipeline.py：IMAGE/WS_ROOT 改读 env（BIO_IMAGE/
BIO_WORKSPACE_ROOT，与 settings 同口径）+ --name bio-skill-<uuid12>
+ TimeoutExpired 兜底 docker kill（对齐 BioRunner 2026-09-12 治理）。

### D4 跨镜像分发特例保留 [现状追认]

st_cellchat_v2 走 bio 镜像 + /opt/sc_tools（CellChat v2 R 栈单点
安装）。后续同类（R 依赖工具）沿用该模式：**镜像选择跟着依赖栈走，
不跟着 sc/st 前缀走**；在 handler 注释与本 spec 钉注。

### D5 超时档位不强行归一 [现状追认]

600/1200/1800/3600 四档 + st_deconvolve 参数化档混合——档位差异
反映真实计算成本（scenic 40min 级 vs plot 秒级），强行归一要么
浪费墙钟要么回归误杀。research 墙钟（research_sc_timeout_sec=3600）
与单步 3600s 贴边的挤压问题：挂账①已验证降级（见 §3）。
全量口径统一为附录 A 一张表（双通道），测试守护防漂移。

## 3. 挂账收官（2026-09-17 三项全落地）

- ① **收尾墙钟挤压 → 已验证降级，关闭**。sent 真机复验
  （12 ImageBlock 量级，每图 3 次 API）实测：create 1.55s /
  render 30.87s / grant 0.65s，合计 ≈33s，占 3600s research 墙钟
  0.9%——重报告收尾不构成挤压。重评触发条件（任一即重开）：
  单报告图量 ≥60、render 阶段 >120s、或单步顶满 3600s 与
  重报告并存。
- ② **双通道超时口径表 → 已落地**（附录 A）：L3 40 工具四档 +
  skill 编排每步 3900s + research 墙钟 + BioRunner 兜底，一表化；
  skill 侧超时提取为常量 `STEP_TIMEOUT_SEC`、容器资源改 env
  口径（BIO_CPUS/BIO_MEMORY，与 settings 同源）。表由
  test_l3_dispatch_contract.py 表守护测试钉死：registry 动态值 /
  skill 源码常量 / settings 默认三方 vs 文档表，任一漂移即红。
- ③ **工具级资源覆写 → 已落地**：ToolSpec 增 `cpus`/`memory`
  （None=走全局默认），BioRunner.run 透传 docker --cpus/--memory，
  合同测试与 timeout 同模式断言"声明必透传"。首批声明（宿主
  64 GB/24 核、docker 可用 55 GiB，32g 覆写余量充足）：
  - sc_cellchat_v2 / st_cellchat_v2：memory=32g（Phase 61 真机
    59900×38 类矩阵内存峰值依据，曾单次稠密化 11 GiB 翻车）；
  - sc_scenic：cpus=8 + memory=32g（dask n_workers 内存随 worker
    线性涨，解锁 >4 worker 上限；description 已同步 32g 口径）。

## 4. 验证记录

- 合同测试 1 passed（41 工具全量断言；回滚任一 timeout 即红）；
- pytest 全量 1326 passed（-m "not pg"）、ruff 绿、mypy 双平台
  193 文件 0 错；
- bio/st 镜像 COPY 层 rebuild + 卫兵冒烟（同轮 ⑥ 项一并验证）。

挂账收官轮（2026-09-17）补充：

- 合同测试扩 cpus/memory 断言（40 工具）+ 表守护测试（附录 A 三方
  比对）+ BioRunner 覆写透传单测；
- 宿主容量实测：64 GB / 24 逻辑核，docker 可用 55 GiB / 24 CPU。

## 附录 A：双通道超时口径表（挂账②，测试守护）

> 守护测试：tests/unit/test_l3_dispatch_contract.py
> （registry 动态值 / skill 源码常量 / settings 默认 vs 本表，
> 任一漂移 CI 即红；改超时必须同步本表）。

L3 通道（ToolSpec.timeout_sec → handler 透传，合同测试双钉）：

| 档(s) | 工具 |
|---|---|
| 600 | sc_load, sc_qc, sc_plot, sc_cellfreq, sc_meta, sc_cellcycle, st_load, st_qc, st_plot, st_niche, st_vicinity, st_trajectory |
| 1200 | sc_markers, sc_pseudotime, sc_de, sc_deconv, sc_doublet, sc_wnn, sc_nichenet, st_markers, st_domains |
| 1800 | sc_process, sc_enrichment, sc_score_genes, sc_metabolism, sc_subcluster, sc_integrate, sc_annotate, sc_cytotrace2, st_process, st_commot, st_stats, st_misty, st_nichenet, st_integrate |
| 3600 | sc_cellchat, sc_cellchat_v2, sc_milo, sc_scenic, sc_knockout, sc_cnv, st_deconvolve*, st_cnv, st_cellchat_v2, st_niche_scan |

- \* st_deconvolve 为参数化档：handler 由
  settings.st_deconvolve_timeout_sec 注入（默认 3600）。
- 工具级资源覆写（挂账③）：sc_scenic（cpus=8, memory=32g）、
  sc_cellchat_v2 / st_cellchat_v2（memory=32g）；其余工具走全局
  settings.bio_cpus（默认 4）/ bio_memory（默认 16g）。

skill / 框架通道：

| 执行点 | 档(s) | 来源 |
|---|---|---|
| skill bio_trajectory_pipeline 每步 | 3900 | run_pipeline.py 常量 STEP_TIMEOUT_SEC |
| research 墙钟（plan 含 sc_*/st_*） | 3600 | settings.research_sc_timeout_sec 默认 |
| BioRunner 兜底默认（handler 未传时） | 900 | settings.bio_script_timeout_sec 默认 |
