# Phase 17 设计：节点级 L2 审批（research 链路开放 write_doc）

日期：2026-09-01
状态：已定稿（先 spec 后实施）

## 1. 背景与问题

- Phase 14 起 research 链路（/research）的 L2 副作用工具**整体不可规划**：
  `research_runner._execute` 把 `risk_level == "L2_side_effect"` 的工具从
  planner 可见列表整体剔除。研究结果只能由 Runner 在任务收尾时**整体写回**
  （card_confirm 卡片确认）。
- 后果：模型无法表达"把某个中间结论写进文档"这类**任务内副作用**——
  例如"检索 BRCA1 → 筛选 → **把筛选结论追加到绑定文档** → 继续分析"。
  写文档只能是"全部跑完后一次性写入"，粒度太粗。
- ToolHandler 里的 L2 approval stub（`ApprovalService.request_sync`，
  Phase 2 mock：bind 覆盖→True，否则 False）因 L2 不可规划而**不可达**，
  属于过时代码（真机 2026-08-30 n3 TOOL_DENIED 即它所致）。

## 2. 目标 / 非目标

**目标**
1. research planner 可规划 `write_doc` 节点（其余 L2 工具仍不可规划）。
2. `write_doc` 节点执行前发**审批卡片**：任务发起者 approve → 节点执行；
   deny / 超时 → 节点 DENIED（下游 SKIPPED，安全侧失败）。
3. `doc_id` 强制等于绑定文档（gate 校验），杜绝写任意文档。
4. plan 中含 write_doc 节点时，Runner 收尾**跳过自动写回**（防双写）。
5. 复用既有机制：ApprovalBroker 跨线程决策、doc_writes 表（owner 校验 +
   审计 + 启动清扫）、gateway 卡片回调分支。

**非目标**
- 不开放 send_card / write_base_projection / upload_drive 给 planner
  （参数 app_token/chat_id/local_path 模型易编造，价值低；留待有真实需求再议）。
- 不做异步 gate（审批等待期间 scheduler 轮询循环阻塞——L2 节点通常在
  DAG 末端，实际影响小；wall-clock 超时兜底。优化留待后续）。
- 普通聊天链路（process_phase2）不开放 L2 规划。

## 3. 方案

### 3.1 Planner 可见性（research_runner）

```python
# 原：L2 整体剔除
# 新：开关开启 + broker 可用时，L2 中只放行 write_doc
allow_l2 = (getattr(settings, "research_allow_node_l2", True)
            and broker is not None)
visible = [
    t for t in registry.list(planner_visible=True)
    if t.name not in disabled
    and (t.risk_level != "L2_side_effect"
         or (allow_l2 and t.name == "write_doc"))
]
```

`session_context`（已绑定文档时）追加提示：
"如需把结果写入绑定文档，可规划 write_doc 工具节点（doc_id 用绑定文档
id，blocks 用上述渲染格式）；该节点执行前会发卡片请求用户确认。"

### 3.2 Scheduler 注入 l2_gate（scheduler.py）

- 构造参数新增 `l2_gate=None`：`Callable[[DAGNode, dict], tuple[bool, str]]`
  （返回 (approved, error_code)）；None = 不设门（现行为不变）。
- `_run_phase2_loop` 提交 tool 节点**前**：

```python
if self.l2_gate is not None and node.kind == "tool":
    spec = self._tool_spec(node.tool_name)   # 由 gate 注入方提供查询
    if spec is not None and spec.risk_level == "L2_side_effect":
        ok, code = self.l2_gate(node, resolved_inputs)
        if not ok:
            self._handles[node.node_id] = TaskHandle(
                ..., state=ExecutionState.DENIED,
                error_code=code, error_message="denied by approval",
            )
            continue
```

- scheduler 不查 registry（保持无依赖）：gate 判定 L2 与否的职责在注入方——
  research_runner 的 gate 闭包内部先判断 `tool_name != "write_doc"` 直接
  放行（非 write_doc 的 L2 不会出现在 plan 里，防御性放行无副作用）。
  简化：**gate 只对 write_doc 生效**，scheduler 对所有 tool 节点调 gate，
  gate 自行短路。频率开销可忽略（每节点一次 dict 查询）。
- `_collect_result`：DENIED 计入 partial（与 SKIPPED 同档）：

```python
skipped = any(s in (SKIPPED, DENIED) for s in node_states.values())
```

### 3.3 research_runner 的 gate 实现

```python
def _l2_gate(self, node, inputs) -> tuple[bool, str]:
    if node.tool_name != "write_doc":
        return True, ""                       # 防御性放行
    # 1. doc_id 必须 = 绑定文档
    if inputs.get("doc_id") != self._bound_doc:
        return False, "TOOL_DENIED"           # 不发卡，直接拒（模型编造 doc）
    # 2. 落 pending（owner 校验 + 审计 + 启动清扫复用）
    pending = svc.create_confirm_pending(..., approval_mode="node_l2",
                                         preview_text=参数摘要)
    # 3. 发审批卡片（action=node_l2_approval，含 doc_write_id + 节点信息）
    # 4. broker.wait(doc_write_id, approval_timeout_sec)
    #    approve → transition(approved)；记 node_id→doc_write_id 待收尾；
    #             return True, ""
    #    deny    → transition(cancelled)；return False, "TOOL_DENIED"
    #    timeout → transition(cancelled)；return False, "APPROVAL_TIMEOUT"
```

- 卡片发送失败 → transition(cancelled) + return False（安全侧失败）。
- scheduler 收尾后（步骤 3.5 渲染前）：遍历 `node_id → doc_write_id` map，
  按节点终态补 doc_writes 状态机：SUCCESS → writing→mark_success；
  FAILED/DENIED → mark_failed（transition(writing) 后失败或直接
  mark_failed——见 3.4 简化：approve 时只 transition(approved)，收尾时
  SUCCESS→transition(writing)+mark_success，否则 mark_failed）。

### 3.4 doc_write_service 小改

`create_confirm_pending` 新增参数 `approval_mode: str = "card_confirm"`。
node 审批传 `"node_l2"`。`cancel_stale_pending` 启动清扫同时覆盖两种
mode 的孤儿 pending（query 条件去掉 mode 过滤，或 in_ 两种）。

### 3.5 gateway 卡片回调（app.py）

`process_card_payload` 的 `research_writeback` 分支扩展为
`action in ("research_writeback", "node_l2_approval")`：
同一处理逻辑（audit 落库已通用；owner 校验 `_doc_write_owner` 复用——
node 审批也落 doc_writes.requested_by；broker.decide 幂等）。

### 3.6 自动写回去重（research_runner 步骤 4）

```python
has_write_node = any(n.kind == "tool" and n.tool_name == "write_doc"
                     for n in plan.nodes)
if has_write_node:
    # 模型已规划写文档节点（无论其终态），收尾不再自动写回
    writeback_note = "写回已由 write_doc 节点执行（跳过自动写回）"
```

### 3.7 ToolHandler 清理

删除 execute 的 L2 approval stub 分支（request_sync mock 不可达且过时）
及构造参数 `approval_service`；app.py 同步删 `self.approval` 装配与
`ApprovalService` 导入。ApprovalService 类保留（webhook 验签
verify_callback 仍在用，gateway/app.py:319）。

### 3.8 settings

```python
# Phase 17：research 链路节点级 L2 审批（write_doc 卡片确认）；False 时
# 回到 Phase 14 行为（L2 整体不可规划 + 收尾整体写回）
research_allow_node_l2: bool = True
```

## 4. 交互样例

用户：`/research 检索 BRCA1 human 蛋白数量，并把检索结论追加到绑定文档`
→ 模型规划：n1 blast_search → n2 run_python(统计) → n3 write_doc(doc_id=绑定文档)
→ n3 就绪时发卡片：「节点 n3 请求写入绑定文档（write_doc）｜参数摘要…｜[同意执行] [跳过]」
→ approve：n3 执行写入 → 任务 success → IM 摘要（不再自动写回）
→ deny：n3 DENIED → 下游 SKIPPED → partial + IM 提示"已按您的选择跳过写入"

## 5. 测试计划

单测：
- scheduler：gate deny → 节点 DENIED + 下游 SKIPPED + status partial；
  gate approve → 节点正常执行；无 gate → 行为不变。
- doc_write_service：mode 参数落库 + cancel_stale_pending 覆盖两种 mode。
- research_runner（集成级 fake）：doc_id 不符不发卡直接拒；approve 后
  doc_writes 状态机收尾 success；deny → 节点 DENIED + 自动写回跳过。
- gateway：node_l2_approval 回调 owner 校验 + broker 决策（复用既有
  test_renew_card_callback 模式）。
- 回归：全量 pytest + ruff。

真机验收（暂缓，按用户要求）：写文档节点全链路 + 双写防护。

## 6. 风险

- 审批等待阻塞 scheduler 轮询（见非目标）：wall-clock 超时兜底。
- 模型在 write_doc 里编 blocks 结构：write_doc handler 走
  append_blocks 真实 API，块结构非法会节点 FAILED（自愈/IM 报错可见）。
- 卡片 decision 与 doc_writes 状态竞态：决策先到暂存（broker 已有），
  wait 取走后 transition；重复点击幂等拒绝（broker 已有）。
