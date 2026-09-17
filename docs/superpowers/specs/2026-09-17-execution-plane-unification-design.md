# 执行面统一设计（Spec）

- 日期：2026-09-17
- 状态：核心项已落地（本 spec 追认 + 挂账未落地项）
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
与单步 3600s 贴边的挤压问题见挂账①。

## 3. 挂账（未落地，按需另立）

- ① **收尾墙钟挤压**：maybe_build_report 同步执行于 research 收尾链
  且受 3600s 墙钟约束——本次复验实测 12 图 render 30.9s，短期无忧；
  若未来单步 3600s + 重报告并存，需评估收尾阶段墙钟豁免或异步化。
- ② **tools.yaml（skill）与 L3 超时口径表**：trajectory 全景 L3 侧
  1200s vs skill 编排 3900s/步——skill 侧五步串行天然更久，非漂移；
  若后续 skill 编排引入并行或单步超 3900s 再评估。
- ③ **BioRunner 参数面收口**：cpus/memory 目前 settings 全局一份，
  工具级覆写（如 scenic 要更多 worker）暂无需求，不预建。

## 4. 验证记录

- 合同测试 1 passed（41 工具全量断言；回滚任一 timeout 即红）；
- pytest 全量 1326 passed（-m "not pg"）、ruff 绿、mypy 双平台
  193 文件 0 错；
- bio/st 镜像 COPY 层 rebuild + 卫兵冒烟（同轮 ⑥ 项一并验证）。
