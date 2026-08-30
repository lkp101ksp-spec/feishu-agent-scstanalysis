# Phase 13 · T3 控制流执行（branch/while/for Scheduler 解释器）设计

日期：2026-08-30 ｜ 状态：已完成（自动化 + 真机三场景验收均通过） ｜ 前置：Phase 13 T2 已全绿（run_python 真沙箱）

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

## 7. 实施结果（2026-08-30）

四板块全部落地（commits `efa229a`/`2df63af`/`af6acbd`/`767cccf`/`20aa859`/`bb18638`/`b55f69c`）：

| 项 | 结果 |
|---|---|
| Scheduler 控制流解释器 | branch（LLM 判定→展开选中分支）/ for（iterate_over→N 份 body 副本+`{item}` 替换）/ while（重入式逐轮判定+max_iter 硬顶）；副本 id 前缀+依赖重写+控制流字段传递 |
| LLM 判定器 | `_ask_bool`（中英文措辞容错，负向词优先）；成功路径 INFO 留痕 |
| Planner prompt | branch/for/while 完整示例 + `{item}` 用法 |
| 装配 | research_runner 注入 condition_llm（orch.llm） |

**验收**：全量 **651 passed + ruff clean**（Phase 13 T2 结束 635，+16 控制流单测）。

### 真机验收 ✅（三场景，六轮迭代修复）

| 场景 | 结果 |
|---|---|
| for：BRCA1 蛋白记录逐条摘要 | ✅ n1 blast 5 条 → `[f1.iterations] 5` → 5 个副本各自摘要（`{item}` 注入生效） |
| branch：绑定文档 >500 字则摘要 | ✅ `[b1.chosen] true_branch` + true 分支副本摘要执行、写回文档 |
| while：逐步生成随机数直到 ≥0.99 | ✅ do-while 首轮免判定 + 11 轮逐轮判定收敛停止 |

注：for/while 首版任务（固定三项计算、单脚本自循环）被模型合理地用单 run_python 平铺完成——任务本身在 Python 内可闭环，非引擎问题；换成「项数运行时可知」「逐步执行」措辞后模型即生成控制流节点。

### 真机六轮迭代修复（本轮核心价值）

1. **planner 失败无原始响应**：JSON 坏输出只看到错位不看到内容 → 失败留痕 raw head 日志 + 重试 1→2（`2df63af`）
2. **判定上下文整体截断**：block_tree 巨大字段淹没 text，长度类条件失据 → 分字段格式化 + `text<共N字符>` 长度元数据（`af6acbd`）
3. **code 内嵌引用不生效**：模型写 `len('n1.text')` 算了字面量长度 7 → `_inline_substitute`（整值引用保持原语义；引号内形态只换内核防双层引号语法错）（`767cccf`）
4. **branch 分支内嵌 while 副本丢控制流字段**：validate_dag 只禁循环体互嵌，branch 内嵌 while 合法 → 副本完整传递 condition_prompt/body 等（`20aa859`）
5. **while 判定引用名字错位**：判据写 `n_step.result` 而上下文是 `w1_r0_s1.result` → 展开轮记录 id 映射、判定 prompt 引用重写对齐（`bb18638`）
6. **while-do vs do-while 语义**：「直到…为止」类任务 w1 无上游、首轮上下文空 → LLM 无据判 false 0 轮退出 → 首轮无上游数据时免判定先执行一轮（`b55f69c`）

### 已知局限

- 嵌套控制流的跨子树引用（branch 内 while body 引用 branch 的另一兄弟节点）在副本 id 重写后失配——边缘场景，模型少见生成，遇失败可见
- while 每轮一次 LLM 判定（~2s），20 轮上限下任务整体 timeout 兜底
