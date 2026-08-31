# Phase 14 设计：L2 写回审批流（卡片确认）

- 状态：已完成（自动化 + 真机三场景验收均通过，2026-08-31）
- 日期：2026-08-30
- 前置：Phase 12（科研执行主链路）/ Phase 13 T2（沙箱）/ T3（控制流）均已完成真机验收

## 1. 背景与现状

Phase 12 spec「遗留（Phase 13）」清单第 3 条：**L2 审批流——研究任务内需写文档时的正式审批链路（当前由 Runner 自动写回替代）**。

### 现状缺口

| 层 | 现状 | 问题 |
|---|---|---|
| research_runner 写回 | 任务完成后直接 `doc_adapter.render_blocks(bound_doc, blocks)` | 绕过 DocWriteService——无 doc_writes 记录、无审批、无审计，Agent 未经确认直接改用户文档 |
| ApprovalService.request_sync | Phase 2 stub：bind_doc 覆盖 → True，否则 False | 无真实卡片审批实现 |
| doc_writes 状态机 | pending → approved → writing → success/failed 已存在，但唯一使用方（DocWriteService.write_plain_text）固定 `approval_mode="bind_scope"` 自动批准 | 状态机就绪，缺 card_confirm 模式 |
| 卡片回调链路 | ws_client `P2CardActionTrigger` → gateway/app `handle_card_action` → orchestrator/app 分支（renew_bind 在用） | 链路已通，加 action 分支即可复用 |

### 范围决策

**本轮只做「Runner 尾部写回审批化」**——研究任务执行完成后的文档写回从自动直写改为卡片确认。

节点级 L2 工具审批（write_doc 等重新对 planner 开放 + Scheduler 运行中挂起等待外部事件）**不在本轮**：Scheduler 目前是 `run_until_done` 一次性跑完，挂起等回调是架构级改动；且当前唯一真实写回路径就是 Runner 尾部，先补齐它即闭环。

## 2. 设计

### 2.0 总览（时序）

```
research 线程                         ws 回调线程（用户点按钮）
──────────                         ──────────────────────
任务执行完成
  ├ doc_writes: create_pending(card_confirm)
  ├ 发审批卡片（内容预览 + 同意/跳过按钮）
  ├ broker.wait(doc_write_id, timeout) ──阻塞──┐
  │                                            │ 用户点击 → handle_card_action
  │                                            │   ├ broker.decide(doc_write_id, approve/deny)
  │◄───────────────────────────────────────────┤   └ IM 回执（已写入/已跳过）
  ├ approve: transition(approved→writing)
  │   → render_blocks 写入 → mark_success
  ├ deny/timeout: transition(cancelled)
  └ IM 最终摘要（含写回结果行）
```

### 2.1 审批模式配置

Settings 新增：

```python
research_writeback_approval: str = "card_confirm"   # card_confirm | bind_scope
research_approval_timeout_sec: int = 600            # 审批卡片等待时长
```

- 默认 `card_confirm`（Phase 14 的意义所在）；`bind_scope` 保留为兼容开关（切回即回到现状自动写回行为，作回归基线）
- 超时 600s：10 分钟无人处理按拒绝处理（安全侧失败）

### 2.2 ApprovalBroker（跨线程决策传递）

新文件 `orchestrator/approval_broker.py`：

```python
class ApprovalBroker:
    """doc_write_id → 决策事件，跨线程传递卡片审批结果（单进程 MVP）。"""

    def wait(self, doc_write_id: str, timeout: float) -> str | None:
        """阻塞等待决策，返回 "approve" / "deny"，超时返回 None。"""

    def decide(self, doc_write_id: str, decision: str, operator: str) -> bool:
        """回调线程调用；首次决策生效返回 True，重复/未知返回 False。"""
```

实现要点：

- 内部 `dict[doc_write_id, threading.Event]` + 结果字典；`decide` 首次 `set` 后即固化（幂等，重复点击不二次生效）
- `wait` 返回后清理条目（防泄漏）
- 挂在 app 级单例（orchestrator/app.py 装配），research_runner 与 card action 分支共用
- 单进程 MVP 内存实现；多进程/重启场景由超时兜底（卡片点了他端无人接 → 超时取消）

### 2.3 DocWriteService 扩展：card_confirm 流程

`DocWriteService` 新增 `write_with_confirm(...)`（保持 `write_plain_text` 原语义不动）：

1. 校验 bind 有效性（复用现有逻辑）
2. `create_pending(approval_mode="card_confirm", approval_id=doc_write_id)` 
3. **由调用方（research_runner）发卡片并 broker.wait**——服务层不持有 IM/broker，保持纯粹；wait 结果作为参数回传
4. approve → `transition(approved)` → `transition(writing)` → 写入 → `mark_success` / 异常 `mark_failed`
5. deny/timeout → `transition(cancelled)`

状态机复用现有五态，无表结构变更（approval_mode 是字符串字段，新增枚举值 "card_confirm"）。

### 2.4 审批卡片与回调分支

**卡片**（复用 im_adapter.send_card，与 renew_bind 同构）：

```
header: 研究任务完成——是否写入文档？
elements:
  - 任务摘要（task_id、状态、节点数）
  - 写入内容预览（template.render_blocks_to_text(blocks) 截断 ~800 字符）
  - 按钮【同意写入】 value={action:"research_writeback", doc_write_id, decision:"approve"}
  - 按钮【跳过】     value={action:"research_writeback", doc_write_id, decision:"deny"}
```

**回调分支**（gateway/app.py `handle_card_action` 加 research_writeback，路由到 orchestrator/app.py 新分支）：

1. audit 落库（operator open_id + action + doc_write_id）
2. `broker.decide(doc_write_id, decision, operator)` → False（未知/已决策）则 IM 回「该审批已处理或已过期」
3. True 则由 research 线程完成后续写入与回执（回调分支不再做事，避免双线程写文档）

### 2.5 research_runner 接线

第 4 步「绑定文档则写回」改为：

```python
if bound_doc:
    if mode == "bind_scope":
        现状逻辑不变（render_blocks 直写）
    else:  # card_confirm
        preview = render_blocks_to_text(blocks)
        发卡片 → decision = broker.wait(doc_write_id, timeout)
        approve → DocWriteService.write_with_confirm(...) 落库写入
        deny/timeout → transition(cancelled) + IM 提示（任务本身仍 success）
```

IM 最终摘要追加一行：`写回：已写入 / 已跳过 / 已超时跳过 / 写入失败`。

## 3. 失败语义

| 情形 | doc_writes 终态 | 任务状态 | IM 提示 |
|---|---|---|---|
| 用户同意 + 写入成功 | success | success | 摘要 +「已写入文档」 |
| 用户同意 + 写入异常 | failed | success | 「写入失败：{reason}」 |
| 用户点跳过 | cancelled | success | 「已按您的选择跳过写回」 |
| 超时无人处理 | cancelled | success | 「审批超时（{N}分钟），已跳过写回」 |
| 卡片发送失败 | cancelled | success | 「写回卡片发送失败，已跳过」 |
| bind 已过期 | 不建记录 | success | 「绑定已过期，请 /bind-doc 后重试」（兼修 T3 遗留建议 1 的提示缺失） |

原则：**写回是附属动作，任何审批分支都不改变研究任务本身的 success 结论**。

## 4. 测试策略

- **单测**
  - ApprovalBroker：正常决策 / 超时 None / 重复 decide 幂等 / wait 后清理
  - DocWriteService.write_with_confirm 三分支（approve→success、deny→cancelled、写异常→failed）
  - 卡片 payload 构造（action/decision/doc_write_id 字段）
  - research_runner：bind_scope 回归不变；card_confirm 各分支的 IM 文案
- **集成**：模拟卡片回调（直接调 handle_card_action payload）→ broker.set → research 线程唤醒写入（FakeDocAdapter 断言 anchor）
- **真机三场景**：①同意 → 文档出现摘要 + doc_writes success + audit 有 operator；②点跳过 → 文档无新内容 + cancelled；③不点等超时 → cancelled + 超时提示
- 回归：全量 pytest + ruff

## 5. 验收标准

1. `/research` 任务完成 → 收到审批卡片（含任务摘要与写入内容预览），doc_writes 落 pending（approval_mode=card_confirm）
2. 点「同意写入」→ 文档写入成功，doc_writes success，audit 记录 operator 与 decision
3. 点「跳过」→ 不写入，doc_writes cancelled，IM 提示
4. 超时 → 不写入，doc_writes cancelled，IM 超时提示
5. 重复点击按钮 → 第二次提示「已处理」，不产生二次写入
6. `research_writeback_approval=bind_scope` 时行为与 Phase 13 完全一致（回归）
7. 全量测试通过 + ruff clean

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| research 线程阻塞最长 10 分钟，多任务并发线程堆积 | MVP 单机低并发可接受；daemon 线程不阻塞主进程；后续可改异步 |
| ws 断连期间用户点按钮，回调丢失 | 超时兜底为取消（安全侧失败） |
| 卡片被删除/淹没找不到 | 超时兜底；卡片头带 task_id 可检索 |
| 非会话所有者点按钮（群聊场景） | MVP 记录 operator 入审计不强制拦截（当前主用单聊）；列后续建议 |
| broker 内存态与 doc_writes 状态漂移（如重启） | 重启后 pending 卡片无人 wait → 超时路径取消；启动时可将遗留 pending 置 cancelled（启动清扫，一行） |

## 7. 实施结果（2026-08-31）

### 实施板块（全部按 §2 设计落地）

| 板块 | 落地 |
|---|---|
| ApprovalBroker | [approval_broker.py](../../orchestrator/approval_broker.py)：Event 跨线程唤醒；决策先到暂存（发卡后秒点）；首次决策固化幂等；wait 取走即清理 |
| Settings | `research_writeback_approval`（默认 card_confirm）/ `research_approval_timeout_sec`（默认 600），均带 env 覆盖 |
| DocWriteService | `create_confirm_pending`（bind 校验 + pending 落库）/ `complete_confirmed`（approve→writing→success/failed；deny/timeout→cancelled）/ `cancel_stale_pending`（启动清扫孤儿 pending）；bind 校验抽 `_valid_bound_doc` 复用 |
| 卡片回调 | gateway `process_card_payload` 加 research_writeback 分支（audit 落 operator+decision，target_id 回退 doc_write_id）；AppContext/create_app 透传 broker |
| research_runner | 双模式接线：card_confirm 走 `_writeback_with_confirm`（DocWriteService 用本线程 session 构造，不跨线程共享主 Session）；bind_scope/broker 缺失降级直写（回归基线不变） |
| runtime 装配 | broker 单例挂 orch + ctx；启动清扫遗留 pending |

### 真机验收 ✅（三场景 + 1 项真机发现修复）

| 场景 | 结果 |
|---|---|
| ① 同意写入 | ✅ 审批卡片（内容预览+按钮）→ 点同意 → broker 决策（日志留 operator）→ render_blocks 写入文档末尾 → IM「已写入文档（经卡片确认）」→ doc_writes card_confirm/success |
| ② 跳过 | ✅ 点跳过 → 不写文档 → IM「已按您的选择跳过写回」→ doc_writes cancelled |
| ③ 超时 | ✅ 不点等待超时 → 不写文档 → IM「审批超时…已跳过写回」→ doc_writes cancelled |

**真机发现与修复**：用户反馈「文档看不到研究结果（1048576）」——写回只含节点状态表，关键输出只在 IM。修：`render_plan_summary_blocks` 加 `outputs` 参数，文档新增「关键输出」小节（heading3 + 列表），复验 3^15=14348907 正确落文档。

**排障实录**：①用户称「没写入」→ 拉 doc_writes（card_confirm/success）+ 直连 API 拉文档块列表，证明写入在文档末尾（288 块），用户未滚到底；②用 ws 日志 `research plan`（1 节点 run_python code="2**20"）+ `approval decided`（doc_write_id 与 DB 一致）闭环证据链。

### 验收总览

- 新增/修改测试：test_approval_broker（6）+ test_doc_write_service（+7）+ test_renew_card_callback（+3）+ test_research_runner（+5，含 bind_scope 回归）→ 全量 **672 passed + ruff clean**（Phase 13 T3 结束 651，+21）

### 已知局限（列后续）

- `render_blocks` 不返回块 id → card_confirm 写入的 anchor_block_id 为空（不影响写入与展示，只影响后续「锚点续写跟随」定位到本次写入）
- 卡片预览为 render_blocks_to_text 纯文本（表格转逗号分隔），富表格预览待做
- 重复点击按钮仅日志留痕 + 返回 already_handled，无用户可见 toast（卡片回调响应体未接 toast 更新）
