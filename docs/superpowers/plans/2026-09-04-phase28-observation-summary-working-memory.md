# Phase 28：失败观察摘要 + Working Memory（Recuris 思想落地收尾）

日期：2026-09-04
状态：计划已确认，待实施
前置：Phase 27 已收官（诊断闭环真机全通，commit eaf8fd4/875b280）

## 背景

Phase 27 真机验收暴露两个诊断质量问题，根因相同——**诊断器拿到的信息太少**：

1. `LoopResult.tool_events` 只有 `{step, name, ok}`，诊断 LLM 看不到具体
   报错内容（如 `SCRIPT_ERROR: argument parsing failed`），只能瞎猜
   → 幻觉 schema 外字段（已由 875b280 白名单兜底，但治标）
2. 把连败禁用机制终止误读为"agent 直接终止未重试"——同样是看不到
   工具真实观察导致的理解偏差
3. 任务内模型重复试错：同一失败参数反复调用（真机 qc_stat 验收中
   模型试了 3 种路径写法，前 2 种失败信息无跨步记忆强化）

Recuris 的 Working Memory 思想（任务内循环：目标 → 调用 → 执行 →
验证 → 记忆更新）本地化为：失败观察进 events（诊断受益）+ 滚动
失败记忆进 messages（模型受益）。

## 不做（明确排除）

- Validation Gate 自动验证（保持人工审批卡）
- 跨任务 Skill Memory 进化（Phase 27 已覆盖单次诊断写回，不做自动迭代）
- 复杂记忆分层/向量检索

## T1：失败观察摘要进 tool_events（诊断质量提升）

### 代码

`orchestrator/coding/agent_loop.py`：
- `_execute_tool` 返回的观察 dict 已含 `error`/`error_message` 字段
- 事件累积处：失败事件追加 `"error": <摘要>`（从 obs 提取
  `error_message or error or str(obs)`，截断 500 字符）；成功事件不追加
- 注意：`truncate_observation` 已有观察截断，此处独立截断防 events 膨胀

`orchestrator/coding/skill_diagnoser.py`：
- `_condense_events`：失败事件的行 JSON 增加 `error` 字段送入 prompt

### 测试（TDD）

- 单测 AgentLoop：失败事件含 error 摘要（截断生效）/ 成功事件无 error 键
- 单测 diagnoser：prompt 中可见 error 文本（用 Mock llm.chat 捕获）
- 更新现有 fixtures（failed_result 事件可补 error 字段，向后兼容：旧
  事件无 error 键时 _condense_events 正常输出）

## T2：Working Memory 滚动失败记忆（减少重复试错）

### 代码

`orchestrator/coding/agent_loop.py`：
- 维护模块级消息 `{"role": "user", "content": "## 已试路径（避免重复）\n- ..."}`，
  初始不存在；首次工具失败时插入（放在 system 之后）
- 每次失败追加一行 `- step N: <tool>(<args 摘要 100 字符>) → <error 摘要 200 字符>`，
  滚动窗口保留最近 5 行（旧行丢弃）
- `compress_messages` 需识别该消息并保护（不压缩丢弃）——若实现复杂，
  退化为压缩时重建（从 events 重生成），二选一在实施时定

### 测试（TDD）

- 单测：失败后 messages 含"已试路径"消息与错误摘要；连续 2 次不同失败
  → 两行；成功调用不影响该消息
- 单测：滚动窗口 5 行封顶
- e2e：失败场景 messages 传播正常（现有 e2e 不破）

## T3：e2e + 回归收尾

- 新增 e2e：失败任务 → 诊断 prompt（Mock 捕获）包含真实错误文本
- 全量回归，更新 ROADMAP/测试总结，commit

## 验收（真机，可选轻量）

- 重造 qc_stat 桩 skill 失败 → 诊断卡 issue 应引用真实 stderr 文本
  （"argument parsing failed"），而非泛泛的"参数说明不清"
- 验收后删桩

## 风险

- events 变大 → MAX_EVENTS_IN_PROMPT=30 已有上限 + 500 字符截断
- memory 消息与 compress_messages 交互 → T2 明确二选一策略
- token 预算 → 滚动窗口 5 行 × ~300 字符，增量可忽略
