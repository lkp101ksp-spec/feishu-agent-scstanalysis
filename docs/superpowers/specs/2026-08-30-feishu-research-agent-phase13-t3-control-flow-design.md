# Phase 13 · T3 控制流执行（branch/while/for Scheduler 解释器）设计

日期：2026-08-30 ｜ 状态：待批准 ｜ 前置：Phase 13 T2 已全绿（run_python 真沙箱）

## 1. 背景与现状

| 层 | 现状 |
|---|---|
| Schema/校验 | ✅ branch/while/for 字段齐全（`condition_prompt`/`true_branch`/`while_condition_prompt`/`body`/`iterate_over`/`iteration_var`/`max_iterations`），嵌套校验已有（while/for 内禁嵌套 while/for） |
| Planner | ✅ prompt 有控制流描述、`_build_node` 递归解析——模型**可以**生成控制流节点 |
| Scheduler 执行 | ❌ **空白**：控制流节点被当 tool 提交（tool_name 空 → registry 查不到 → FAILED），嵌套子节点永不执行 |
| LLM 条件判定器 | ❌ 不存在 |
| for 变量注入 | ❌ 机制未定义 |

真机风险：模型一旦生成 branch/for 节点即 FAILED + 下游 SKIPPED。T3 = 给 Scheduler 补控制流解释器。

用户决策（2026-08-30）：①三种全做 ②for 用 `{item}` 字面替换注入 ③LLM 判定失败 → 控制流节点显式 FAILED。

## 2. 设计

### 2.0 总览

`Scheduler._run_phase2_loop` 主循环按 kind 分流：tool/llm 走原路径 submit executor；branch/for/while 在调度循环内**同步解释**（展开副本节点追加进 `plan.nodes`，轮询循环自然调度）。循环计数内置（`self._loop_rounds`），不接 PlanRuntime（单次研究任务无需 DB 持久化循环状态）。

### 2.1 展开机制（三种共用）

- **副本 node_id 前缀**：branch `{bid}_t{n}_`（true 分支）/ `{bid}_f{n}_`，for `{fid}_i{n}_`，while `{wid}_r{n}_`（n 为分支内/项/轮序号）——含序号保证多次展开不冲突
- **依赖重写**：子节点 `depends_on` 中指向控制流节点自身 → 替换为控制流节点的 `depends_on`（继承上游）；子树内互相依赖同步加前缀
- 展开副本 append 进 `self.plan.nodes` + 更新 `self._node_map`
- 控制流节点自身 handle：SUCCESS + `outputs` 记元数据（branch → `{"chosen": "true_branch"|"false_branch"}`；for → `{"iterations": N}`；while → `{"rounds": i}`）——`_outputs_digest` 自然呈现执行轨迹

### 2.2 branch

1. 就绪（depends_on 全 SUCCESS）→ 收集直接上游 outputs（JSON 序列化，截断 2000 字符）
2. LLM 判定：system「你是条件判定器，只输出 true 或 false」+ `condition_prompt` + 上游摘要
3. 解析容错：strip/小写/包含 true/false 字样
4. true → 展开 true_branch；false → 展开 false_branch（空分支跳过）→ 节点 SUCCESS
5. LLM 异常/输出不可解析 → FAILED（`error_code=LLM_FAILED`）

### 2.3 for

1. `iterate_over` 按引用解析（`.` 前必须是已知 node_id——沿用 T2 修复的判定）得 list；非 list/字段不存在 → FAILED（`INVALID_INPUT`）
2. `len(items) > max_iterations` → 截断 + warning
3. 每项展开 body 副本，inputs 值中字面 `{iteration_var}`（默认 `{item}`）替换：str 项原样替换、dict/list 项 `json.dumps(ensure_ascii=False)`
4. 节点 SUCCESS（outputs 记 iterations）

### 2.4 while（重入机制）

- 状态：`self._while_state[node_id] = {"round": i, "body_ids": [...]}`（round 0 = 未展开过）
- **就绪门控**：首轮 depends_on 全 SUCCESS；后续轮上一轮 body 全终态
- 判定上下文：`while_condition_prompt` + 直接上游摘要（首轮）/ 上一轮 body 输出摘要（含失败信息，后续轮）
- 判定 true → 展开第 round+1 轮 body 副本 → while 保持无 handle（PENDING，但门控挡住不重复入 ready）→ 该轮终态后再判定
- 判定 false → SUCCESS（outputs 记 rounds）
- round ≥ max_iterations 仍 true → FAILED（`LOOP_MAX_ITER`）
- body 节点失败不直接终止 while——失败信息进入下轮判定上下文，由 LLM 决定是否继续（沿用 on_node_fail=continue 哲学）

### 2.5 LLM 判定器

- `Scheduler(plan, executor, max_concurrent, runtime, condition_llm=None)` 新增可选参数
- 判定函数模块级 `_ask_bool(llm, condition, context) -> bool`（可独立单测）；`condition_llm` 为 None 时（未注入/测试）→ 控制流节点 FAILED（`LLM_FAILED`，消息注明判定器未注入）
- research_runner 装配传入 orchestrator 的 llm_router

### 2.6 Planner prompt 补充（Phase 12 教训：模型需要完整示例）

- `{item}` 用法：body 子节点 inputs 值中字面 `{item}` 在每轮被替换为当前项
- branch / for 各一个完整 JSON 示例；while 简述
- 引导语：简单任务用平铺 tool 节点，仅当需要条件分支/批量遍历/迭代收敛时用控制流

## 3. 失败语义汇总

| 场景 | 结果 |
|---|---|
| LLM 判定异常/不可解析/未注入 | 控制流节点 FAILED（LLM_FAILED）→ 下游 SKIPPED |
| for 的 iterate_over 非 list/缺失 | FAILED（INVALID_INPUT） |
| while 超 max_iterations | FAILED（LOOP_MAX_ITER） |
| 展开的子节点失败 | 控制流节点自身已 SUCCESS 不回改（branch/for 展开职责已完成）；while 下轮判定上下文可见失败 |

## 4. 测试策略

| 层 | 用例 |
|---|---|
| 单测（FakeLLM 预置判定序列 + FakeExecutor） | branch true/false 展开执行；判定失败 FAILED；for 展开 N 项 + `{item}` 注入（str/dict 项）；for 非 list FAILED；for 超 max 截断；while 多轮→false 退出；while max_iter FAILED；while body 失败后继续判定；依赖重写（子树互链 + 指向控制流节点）；condition_llm=None FAILED；控制流 outputs 元数据 |
| 回归 | 635 全过（控制流默认不触发，老路径零影响） |
| 真机 | ① branch：`/research 检查绑定文档：若内容超过 500 字则摘要，否则说明太短` ② for：`/research 用 python 分别计算 2、3、4 的 10 次方，逐个报告结果` ③ while：`/research 让 python 反复生成随机数直到出现大于 0.99 的，报告用了几次（最多 20 次）` |

## 5. 验收标准

- 单测 + 全量回归 + ruff clean
- 真机 branch、for 两场景通过；while 争取通过（模型生成不稳定则记录现象，prompt 调优可下轮）

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 模型不生成控制流（真机任务跑成平铺） | prompt 完整示例 + 任务措辞诱导；真机不生成则换措辞重试 |
| 模型生成非法嵌套结构 | validate_dag 已挡（PLAN_FAILED 可见） |
| while 判定摇摆不收敛 | max_iterations 硬顶 + 研究任务整体 timeout 兜底 |
| 展开副本 node_id 冲突 | 前缀含序号；控制流节点展开后即置 SUCCESS（有 handle 不再 ready）/ while 轮门控 |
| LLM 判定拖慢调度循环 | 同步调用可接受（runner 独立线程 + 整体 timeout 兜底） |
