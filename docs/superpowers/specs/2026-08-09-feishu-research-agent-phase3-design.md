# 飞书科研闭环 Agent — Phase 3 设计稿

> 日期：2026-08-09
> 状态：Phase 3 设计定稿（待实施）
> 范围：在 Phase 2 静态 DAG 基础上引入动态化、循环、上下文压缩、AST 分级、bind_doc 续期
> 前置：[Phase 1 设计稿](./2026-08-08-feishu-research-agent-design.md) / [Phase 2 设计稿](./2026-08-08-feishu-research-agent-phase2-design.md)
> 后置：Phase 3 实施计划 [`../plans/2026-08-09-feishu-research-agent-phase3.md`](../plans/2026-08-09-feishu-research-agent-phase3.md)（待写）

---

## 1. 范围与目标### 1.1 Phase 3 主题

让 LLM 在执行过程中**动态调整 Plan**：分支条件决策后追加节点、循环执行直到条件满足、自动管理长会话上下文、按风险分级提示代码安全性、自动续期 bind_doc 授权。

### 1.2 范围（在 Phase 2 基础上新增）

| 子系统 | Phase 3 内容 | Phase 3 不做 |
|---|---|---|
| PlanRuntime | 抽离 Scheduler 中的 ready / cancel / finish；新增 dynamic-append 入口 | 重写 Scheduler；切 gRPC（Phase 5） |
| DAG 动态化 | branch 节点 condition_eval 调 LLM；为真时向 PlanRuntime.append_nodes() | LLM 决策回退机制（始终成功） |
| DAG 循环 | while（条件 + max_iterations=10）+ for（迭代集合，max=100）| 嵌套 while/for；break/continue 控制流 |
| 上下文压缩 | 80% 触发 LLM 总结；95% 冻结 session 开新会话 | 跨 session 合并历史；选择性遗忘 |
| AST P1/P2 | 仅 audit + IM 提示，不拦截 | 自动改写为 sandbox 安全形式 |
| bind_doc 续期 | 卡片按钮 + `/bind-doc-renew` 指令双通道 | 多用户合签；超时强制重授权 |
| Kernel 序列化 | 不做（Phase 3 评估完成） | — |

### 1.3 关键约束（继承 Phase 1/2）

- bind_doc 仅豁免 `write_doc`（同 doc，30min TTL）；续期不改变豁免范围
- on_node_fail 默认 `continue`；循环节点失败 → 立即退出循环
- AST P0 仍是硬阻塞；P1/P2 仅审计
- 上下文压缩跨 session 时旧 session 状态改为 `archived`
- PostgreSQL 仍为唯一主存；飞书 Base 仅做投影
- 安全边界由 Docker 沙箱兜底；AST 仅提供友好拒绝原因

### 1.4 6 个明确决策记录

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 范围 | 6 项全部（spec §10 完整列表） |
| 2 | DAG 动态追加 | LLM 决策（condition_eval 轻量模型） |
| 3 | DAG 循环 | while + for 双支持，max_iterations 10/100 |
| 4 | 上下文压缩 | 总结 + 冻结，80%/95% 双阈值 |
| 5 | AST P1/P2 | 仅提示不拦截 |
| 6 | bind_doc 续期 | 卡片按钮 + 指令双通道 |
| 7 | Kernel 序列化 | 不做（推到 Phase 5 评估） |

---

## 2. 总体架构### 2.1 架构图

```text
FastAPI (Phase 2 不变)
  └─ Orchestrator (Phase 2 升级 → process_phase3)
        ├─ PlanRuntime (Phase 3 新)
        │     ├─ append_dynamic_nodes(plan_id, parent_node_id, new_nodes)
        │     ├─ loop_iteration_done(loop_id) → int
        │     ├─ loop_exit(loop_id)
        │     └─ freeze_session(session_id, summary) → new_session_id
        ├─ ContextCompressor (Phase 3 新)
        │     ├─ estimate_tokens(messages) → int
        │     ├─ maybe_compress(messages) → messages（含 summary_block）
        │     └─ freeze_session(session_id, summary) → new_session_id
        ├─ ASTGuard 升级 (P1/P2 提示)
        ├─ BindDocService 升级 (renew + maybe_send_renew_card)
        ├─ Planner 升级 (branch/while/for 节点 + 多 LLM 角色)
        ├─ Scheduler 升级（委托给 PlanRuntime，runtime=None 时走 Phase 2 路径）
        ├─ SessionService 升级 (freeze + 跨 session 继承 bind)
        ├─ BackgroundTaskRunner (Phase 3 新，简单协程轮询)
        ├─ Tool Framework + Executor + Drive + Template (Phase 2 不变)
        └─ Feishu Adapter + LLM Router (Phase 2 升级，新增 3 个 LLM role)
```

### 2.2 Runtime 与 Scheduler 的协作

```text
旧路径 (Phase 2):
  Scheduler.run_until_done()
    └─ 内部循环：_ready_nodes() → executor.submit() → _refresh_running_handles() → collect

新路径 (Phase 3):
  Scheduler.run_until_done()
    └─ PlanRuntime.run()
         ├─ 启动 / 恢复 state（从 plan_runtime_state）
         ├─ 事件循环：
         │   ├─ Runtime.get_ready_nodes() → Scheduler.submit_node(task)
         │   ├─ Executor 反馈状态 → Runtime.update_node_state()
         │   ├─ branch 节点完成 → Runtime.append_dynamic_nodes(true_branch)
         │   ├─ while 每轮结束 → Runtime.loop_iteration_done(loop_id)
         │   └─ Runtime 状态 terminal → 退出
         └─ 返回 PlanResult
```

Scheduler 仅保留"提交任务 + 终态汇总"职责；动态化、循环、冻结三件事全部由 Runtime 接管。

### 2.3 关键不变量（全局）

- 所有副作用操作在 audit_logs 留痕
- bind_doc 续期不缩短有效期（new_expires = max(now+30min, current)）
- 冻结时新 session 继承原 bind_doc（如果未过期）
- 跨 session 时旧 session 状态置 `archived`

---

## 3. PlanRuntime

### 3.1 目标

把 Scheduler 中"轮询 ready → 提交 → 状态机推进"提出来，让 Runtime 接管 3 类 Phase 3 新行为：
1. **动态节点追加**：branch 条件为真时插入新节点
2. **循环节点**：while 条件为真时反复触发同一组节点（限 max_iterations=10）
3. **session 冻结**：超上下文时把 session 标 archived，开新 session

Scheduler 退化为 Runtime 的驱动循环（事件循环驱动 / 协程轮询两种都可，Phase 3 沿用协程轮询）。

### 3.2 Runtime 接口

```python
class PlanRuntime:
    def __init__(self, scheduler, executor, llm_router, audit_repo,
                 plan_runtime_state_repo): ...

    # 1) 主循环入口
    async def run(self, plan: DAGPlan) -> PlanResult: ...

    # 2) 动态追加（branch 节点用）
    def append_dynamic_nodes(self, plan_id: str, parent_node_id: str,
                              new_nodes: list[DAGNode]) -> None: ...

    # 3) 循环节点管理
    def loop_iteration_done(self, loop_id: str) -> int:
        """返回当前 iteration 数（>=1）；Runtime 判断是否继续。"""

    def loop_exit(self, loop_id: str) -> None: ...

    # 4) session 冻结
    def freeze_session(self, session_id: str, summary: str) -> str:
        """把 session 标 archived 并开新 session；返回新 session_id。"""
```

### 3.3 动态节点追加流程

```
branch_node 节点完成 → condition_eval LLM 判定 true_branch 是否执行
  │
  ├─ true_branch 不执行 → 直接 SKIPPED
  │
  └─ true_branch 执行
       └─► Runtime.append_dynamic_nodes(plan_id, parent=branch_node_id,
                                          nodes=true_branch)
            │
            ├─ validate_dag(dag_with_new_nodes)   # 重新校验环 / 悬空
            ├─ 写 plan_runtime_state（带 new_nodes + parent_node_id）
            ├─ audit_logs(action=append_dynamic_nodes, detail={parent, count})
            └─ Scheduler 下一轮轮询时把新节点当 entry
```

**关键不变量**：
- 动态节点 `depends_on` **必须**包含 `parent_node_id`（隐式）；校验时 Runtime 注入
- 新节点不能再以 `parent_node_id` 为输入（避免循环依赖）
- 每次 append 触发一次 `validate_dag`，失败抛 `DAGValidationError`

### 3.4 循环节点状态机

```
loop_node 进入 → iter=1
  │
  ▼ while_loop_eval（LLM condition_eval）→ true / false
  │
  ├─ false → loop_exit() → 下游节点 unblock → 继续正常流
  │
  └─ true → 执行 body 子树 → 全部 success
       │
       ├─ 任一 failed（on_node_fail=continue，loop 不退）
       │  └─ loop_iteration_done() → 进入下一轮
       │
       └─ 全 success → loop_iteration_done()
            │
            ├─ iter < max_iterations → 进入 iter+1
            │
            └─ iter >= max_iterations → 强制 exit + audit_logs(action=loop_max_iter, warning)
```

**关键不变量**：
- `max_iterations` 来自节点 `config.max_iterations`；默认 while=10, for=100
- 循环节点本身记 `loop_iteration` 字段进 `executions`（见 §9）
- 循环 body 内部允许 branch（嵌套 while / for 受 phase 3 上限约束 —— 不允许嵌套 while/for，Phase 4 再做）
- while 条件必须是 callable（LLM 决策），不接受裸 Python 表达式（避免沙箱逃逸）

### 3.5 Runtime 持久化与重试

Runtime 状态落 `plan_runtime_state` 表（见 §9.1）：

**重试规则**（继承 Phase 2）：
- Runtime 重启后从 `plan_runtime_state.state_json` 恢复
- 单个节点重试：executor 层不变，沿用 Phase 2 `max_retries` 配置
- 整个 Plan 重试：用户 `/retry <task_id>` 时 Runtime 调 `append_dynamic_nodes` 重置（保持原 dynamic_nodes）

### 3.6 异常处理

| 异常 | Runtime 行为 |
|---|---|
| validate_dag 失败 | audit_logs(action=dynamic_append_failed) + 该 branch 节点标 failed + 下游 skip |
| while LLM 决策异常 | audit + 退化为 `false`（保守退出循环）|
| while 超过 max_iterations | audit(action=loop_max_iter, warning) + 强制 exit + 节点状态 completed_with_warning |
| Runtime 内部异常 | 整个 Plan 标 failed + 立即发 IM 通知 |

### 3.7 不做

- ❌ 跨 Plan 共享 Runtime（每个 Plan 独立 Runtime）
- ❌ Runtime 状态序列化压缩（用 JSON 直接存，size<10MB 可接受）
- ❌ Runtime 水平扩展（Phase 5 切 gRPC 再考虑）

---

## 4. ContextCompressor

### 4.1 目标

1. 上下文使用率 ≥80% 触发 LLM 总结旧消息
2. 总结后仍超限则冻结当前 session，开新 session 并自动附带"上一会话摘要"
3. 旧 session 状态置 `archived`，仍可查询但不接收新消息

### 4.2 触发阈值

| 指标 | 默认值 | 来源 |
|---|---|---|
| `context_token_budget` | 200,000 | settings（Phase 2 已定义） |
| `compress_trigger_ratio` | 0.8 | settings.context_compress_trigger_ratio |
| `freeze_trigger_ratio` | 0.95 | settings.context_freeze_trigger_ratio |

**两层阈值**：
- **80% 总结**：LLM 把最早 N 条历史消息（除最新 5 条外）总结为 1 个 `summary_block`
- **95% 冻结**：总结后仍超限 → freeze_session()

### 4.3 总结流程

```
LLM 调用前估算 token 数 (tiktoken)
  │
  ├─ < 80% → 直接调用
  │
  └─ ≥ 80% → compress_old_messages()
       │
       ├─ 取除最近 5 条外的所有消息
       │  ├─ LLM role=context_compressor，prompt="将以下对话压缩到500 字以内..."
       │  ├─ 替换为单条 summary_block（kind=summary, ref=summary_id）
       │  ├─ 写 audit_logs(action=compress_history, detail={saved_tokens, ratio_before, ratio_after})
       │  └─ 重新估算
       │
       ├─ < 95% → 继续
       │
       └─ ≥ 95% → freeze_session() → 新 session 注入"上下文已冻结，参考旧 summary"
```

### 4.4 Session 冻结

```
freeze_session(session_id, summary) -> new_session_id
  │
  ├─ 原 session.status = "archived"（不可再接收新消息）
  ├─ 原 session.archived_at = utcnow()
  ├─ 写 session_freezes 表（见 §9.2）
  ├─ audit_logs(action=freeze_session, detail={summary_id, ratio})
  ├─ 新 session:
  │  ├─ session_id = new_ulid()
  │  ├─ owner_open_id = 原 owner
  │  ├─ source_chat_id = 原 chat_id
  │  ├─ bound_doc_id = 原 bind（如果未过期）    # §1.3 关键约束
  │  ├─ bind_expires_at = 原值
  │  ├─ approval_scope = 原值
  │  └─ origin_session_id = 原 session_id
  └─ 返回 new_session_id
```

### 4.5 接口

```python
class ContextCompressor:
    def __init__(self, *, llm_router, session_repo, audit_repo,
                 token_counter=None): ...

    def estimate_tokens(self, messages: list[ChatMessage]) -> int: ...

    def maybe_compress(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """返回压缩后的 messages（可能含 summary_block）；超 95% 抛 FreezeRequired。"""

    def freeze_session(self, session_id: str, summary: str) -> str:
        """冻结旧 session，开新 session 并返回新 session_id。"""
```

### 4.6 风险

| 风险 | 缓解 |
|---|---|
| LLM 总结失真 | 保留 summary_id → 可追溯原文（最新 5 条不参与总结） |
| 冻结时机过晚（写文档时才发现）| freeze 必须在 LLM 调用前；总结 + 冻结失败 → 拒绝 LLM 调用返回 `CONTEXT_OVERFLOW` |
| 新 session 丢失 bind_doc | §1.3 已约束：仅当原 bind 未过期才继承 |

---

## 5. ASTGuard P1/P2 分级

### 5.1 目标

P0 仍硬阻塞（Phase 2）；P1/P2 仅记录 + 提示，不拦截。**安全边界仍靠 Docker 沙箱**。

### 5.2 分级规则

| 级别 | 命中例子 | 处理 |
|---|---|---|
| **P0 硬阻塞** | `os.system` / `subprocess.run` / `socket.socket` / `ctypes.CDLL` / 外部 `pickle.loads` | `ToolBlockedError`，节点 FAILED |
| **P1 网络出口提示** | `requests.*` / `urllib.request.*` / `httpx.*` / `aiohttp.*` | 仅 audit + IM 提示 |
| **P2 文件越界提示** | `open()` 路径不在 `/workspace/` 下、`shutil.copy` 跨主机目录 | 仅 audit + IM 提示 |

### 5.3 提示内容

P1 命中时 IM 卡片：`"⚠️ 检测到网络出口请求，已记录到 audit (audit_id=xxx)。如非必要请关闭。"`

P2 命中时 IM 卡片：`"⚠️ 检测到沙箱外文件访问（路径：xxx），已记录。"`

### 5.4 接口

```python
@dataclass
class ASTReport:
    blocked: bool
    notices: list[tuple[str, str, int]]   # (level, message, line_no)

class ASTGuard:
    BLOCKED_CALLS: set     # P0，不变
    P1_PATTERNS: list       # 网络出口
    P2_PATTERNS: list       # 文件越界

    def check(self, code: str) -> ASTReport:
        """返回 ASTReport(blocked, notices)；blocked=True 时 raise ToolBlockedError"""
```

### 5.5 风险

| 风险 | 缓解 |
|---|---|
| P1/P2 误报（用户正常使用）| 提示，不阻断 |
| IM 提示噪音 | 仅第一次命中时发；后续审计 log 即可 |
| 路径解析（相对路径 / symlink）| Phase 3 仅静态检测绝对路径以 `/workspace/` 开头；symlink Phase 4 |

---

## 6. bind_doc 续期

### 6.1 目标

用户无需重新执行 `/bind-doc <doc_id>`，就能延长 30 分钟授权。

### 6.2 续期模式（双通道）

| 通道 | 触发 | 行为 |
|---|---|---|
| 卡片按钮 | bind_doc 还剩 ≤5 分钟时 IM 自动发卡片 | 用户点"续期 30 分钟"按钮 → 立即续 |
| 手动指令 | 用户发 `/bind-doc-renew` | 立即续；不需要 doc_id 参数（用当前 session 的 bind） |

### 6.3 续期流程

```
BindDocService.renew(session_id) -> expires_at
  │
  ├─ 校验 session.bound_doc_id 存在 → 否则 BindDocInvalidError("no active bind")
  ├─ 新 expires_at = max(now + 30min, 当前 expires_at)   # 不会缩短
  ├─ audit_logs(action=renew_bind, detail={old_expires, new_expires})
  ├─ 触发卡片回调：写 approvals 表 + 落 audit
  └─ 返回新 expires_at
```

### 6.4 续期是否需要审批

- **同 doc 的 write_doc 续期**：bind_doc 已授权，续期本身是"延长已授权范围"，无需额外卡片审批
- **首次 `/bind-doc`**：仍走 Phase 1 的 bind_doc 流程（不需审批）

### 6.5 接口

```python
class BindDocService:
    def renew(self, *, session_id: str) -> datetime:
        """续期当前 session 的 bind；返回新 expires_at。"""

    async def maybe_send_renew_card(self, session_id: str) -> None:
        """剩余有效期 ≤5 分钟的，发续期卡片。"""
```

### 6.6 过期前自动提示

`BindDocService.maybe_send_renew_card(session_id)`：扫描所有 active session，剩余有效期 ≤5 分钟的，发卡片。

**实现**：BackgroundTaskRunner 简单协程轮询（每 60s 跑一次）；Phase 5 切独立 worker。

### 6.7 风险

| 风险 | 缓解 |
|---|---|
| 用户不想续期 | 不点按钮即可；旧 bind 自然过期 |
| 卡片被发到无关 session | 仅 active + bound + 剩余 ≤5min 才发 |
| 重复续期 | 续期操作幂等；新 expires_at = max(now+30min, 当前) |
| 续期被滥用为永久授权 | 每次仅延长 30min；卡片按钮可点多次但每次都审计 |

---

## 7. Planner 升级（branch / while / for 节点生成）

### 7.1 目标

让 Planner（Phase 2）能识别并生成 5 种节点类型：
- `tool`（Phase 2 已有）
- `branch`（Phase 3 新）
- `while`（Phase 3 新）
- `for`（Phase 3 新）
- `join`（Phase 2 已有，Phase 3 增强与 branch 联动）

### 7.2 节点 Schema 扩展

```python
class DAGNode(BaseModel):
    # ... Phase 2 字段 ...
    kind: Literal["tool", "llm", "branch", "while", "for", "join"]   # +while/for

    # === branch 专用 ===
    condition_prompt: Optional[str] = None
    true_branch: Optional[list["DAGNode"]] = None
    false_branch: Optional[list["DAGNode"]] = None

    # === while 专用 ===
    while_condition_prompt: Optional[str] = None
    body: Optional[list["DAGNode"]] = None
    max_iterations: int = 10

    # === for 专用 ===
    iterate_over: Optional[str] = None            # <upstream>.<field> 形式
    iteration_var: str = "item"
    body: Optional[list["DAGNode"]] = None
    max_iterations: int = 100
```

### 7.3 Planner 输出改造

Planner（Phase 2）已经调用 LLM 生成 DAGPlan JSON。Phase 3 调整：

| 改动 | 位置 |
|---|---|
| LLM prompt 增加："可以生成 branch/while/for 节点，body 是嵌套 DAGNode 数组" | `_build_dag_prompt()` |
| tools_schema 不变（branch/while/for 由 LLM 直出） | — |
| 校验升级：`validate_dag` 递归校验嵌套子树 | `dag_schema.validate_dag()` |
| Post-processing：把 JSON 递归构造成嵌套 DAGNode | `_build_plan()` |

### 7.4 branch 节点 LLM 调用

```
branch 节点完成 true_branch 执行前：
  │
  ▼ LLMRouter.call(role="condition_eval",
                   prompt=f"{condition_prompt}\n\n上下文：<upstream_outputs>")
  │
  ├─ 返回 "true" → Runtime.append_dynamic_nodes(true_branch)
  ├─ 返回 "false" → Runtime.append_dynamic_nodes(false_branch)（可为空）
  └─ 异常 / 拒绝 → 退化为 "false" + audit
```

### 7.5 while 节点 LLM 调用

```
while 节点每轮结束：
  │
  ▼ LLMRouter.call(role="loop_eval",
                   prompt=f"{while_condition_prompt}\n\n本次迭代结果：<outputs>")
  │
  ├─ 返回 "continue" → Runtime 触发下一轮
  ├─ 返回 "exit" → Runtime 退出循环
  └─ 异常 → 退化为 "exit"
```

### 7.6 for 节点

```
for 节点进入：
  │
  ├─ Runtime 解析 iterate_over=<upstream>.<field>
  ├─ 上游 outputs 必须 list（否则 DAGValidationError）
  ├─ 对每个 item：
  │  ├─ 注入 iteration_var=item 作为 body inputs 的隐式变量
  │  ├─ 执行 body 子树
  │  └─ 任一失败 → break（默认 on_node_fail=continue 且 break_on_fail=True）
  ├─ max_iterations 限
  └─ 全部完成 → 下游 unblock
```

### 7.7 validate_dag 升级

```python
def validate_dag(plan: DAGPlan) -> None:
    # ... Phase 2 校验 ...
    # 新增：递归校验嵌套子树
    for node in plan.nodes:
        if node.kind in {"branch", "while", "for"}:
            for subtree_attr in ("true_branch", "false_branch", "body"):
                subtree = getattr(node, subtree_attr) or []
                for sub_node in subtree:
                    _validate_subtree(sub_node, parent_id=node.node_id)
```

关键约束：
- 嵌套子树的 `depends_on` 必须**显式包含 parent node_id**（由 Runtime 注入）
- while / for 的 body 内**不允许再嵌套 while / for**（Phase 3 简化；嵌套支持 Phase 4）

### 7.8 接口变更

```python
class Planner:
    def __init__(self, llm_router, *,
                 role_intent="intent_parser",
                 role_dag="dag_builder",
                 role_condition_eval="condition_eval",     # 新
                 role_loop_eval="loop_eval",                # 新
                 role_context_compressor="context_compressor",  # 新
                 max_retries=1): ...
```

---

## 8. Scheduler 委托给 PlanRuntime

### 8.1 重构原则

Phase 2 Scheduler 处理 ready → submit → refresh → collect。
Phase 3 Runtime 接管 ready 节点的动态性（动态追加、循环、冻结），Scheduler 变为：
- 驱动 Runtime.run() 的循环容器
- 仅保留：plan 提交、节点委派给 executor、终态收集
- 退出条件 = Runtime.state == terminal

### 8.2 重构后 Scheduler 职责

```
class Scheduler:
    def __init__(self, plan, executor, runtime=None):
        self.runtime = runtime       # 可选，未注入时走 Phase 2 路径
        self.executor = executor
        self._handles: dict[node_id, TaskHandle] = {}

    async def run_until_done(self) -> PlanResult:
        if self.runtime is not None:
            # Phase 3 路径：Runtime 接管
            return await self.runtime.run(self.plan)
        # Phase 2 路径：保留原逻辑
        ...
```

### 8.3 节点状态变化路径

```
旧路径 (Phase 2):
  Scheduler._refresh_running_handles() → Executor.get_status() → 更新本地 handle

新路径 (Phase 3):
  Runtime.run() → 内部循环推进节点状态 → 调 Scheduler.submit_node(task)
  → Executor.submit() → 节点完成 → Runtime.append_event(state_changed)
  → Scheduler._handles 更新 → 继续推进
```

### 8.4 兼容性保证

- Phase 2 的 4 个 Scheduler 测试 **必须** 继续通过（仅调整内部结构；外部接口不变）
- Runtime 是可选注入：未注入时 Scheduler 走 Phase 2 路径（向后兼容）
- `process_phase2` 调用方式不变；`process_phase3` 新增，走 Runtime 路径

### 8.5 节点终态汇总

```
Runtime 终态条件：
- 所有节点 terminal
- 全部 success → status="success"
- failed + skipped → status="success_with_partial_failure"
- failed only → status="failed"
- loop_max_iter 触发 → status="completed_with_warning"
```

### 8.6 不做

- ❌ Runtime 与 Scheduler 完全解耦（保留双向调用以支持 Phase 5 gRPC）
- ❌ 节点状态主动推送（executor 仍轮询）
- ❌ 多 Runtime 实例并行（一个 Plan 一个 Runtime）

---

## 9. PostgreSQL schema 增量

Phase 3 新增 2 张表 + 3 处字段扩展：

### 9.1 新增 `plan_runtime_state`

| 字段 | 类型 | 说明 |
|---|---|---|
| plan_id | text PK | 关联 plan |
| session_id | text nullable | 跨 session 续期时填新 session_id |
| state_json | jsonb nullable | Runtime 完整状态 |
| status | text | running / terminal / archived |
| created_at | datetime | |
| updated_at | datetime | |

**state_json 形状**：

```json
{
  "loop_counters": {"loop_n5": 3, "loop_n8": 1},
  "dynamic_nodes": [
    {"parent_node_id": "n3", "appended_at": "...", "node_ids": ["n3a", "n3b"]}
  ],
  "freeze_origin_session_id": null,
  "iteration_vars": {"for_n7": ["item1", "item2"]}
}
```

### 9.2 新增 `session_freezes`（冻结记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| freeze_id | text PK | ULID |
| origin_session_id | text | 原 session_id（已 archived）|
| new_session_id | text | 新 session_id |
| summary_id | text nullable | 指向 summary_block |
| trigger_ratio | float | 触发时的比例 |
| created_at | datetime | |

### 9.3 扩展 `executions` 表（Phase 2）

| 字段 | 类型 | 说明 |
|---|---|---|
| loop_id | text nullable | 属哪个 while/for 节点 |
| loop_iteration | int nullable | 当前是第几轮（1-based） |
| dynamic_parent_id | text nullable | 由哪个 branch/loop 动态追加 |

### 9.4 扩展 `sessions` 表（Phase 2）

| 字段变更 | 说明 |
|---|---|
| 新增 `archived_at` | datetime nullable（freeze_session 时填） |
| 新增 `origin_session_id` | text nullable（新 session 时回指旧 session）|
| 新增 `token_count` | int default 0（最近一次估算） |
| `status` 枚举扩展 | active / archived（新增） |

### 9.5 迁移脚本

`migrations/versions/0003_phase3_runtime.py`：

```python
revision = "0003"
down_revision = "0002"

def upgrade():
    # 1. plan_runtime_state
    op.create_table(
        "plan_runtime_state",
        sa.Column("plan_id", sa.Text, primary_key=True),
        sa.Column("session_id", sa.Text, nullable=True),
        sa.Column("state_json", sa.JSON, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_prs_session_id", "plan_runtime_state", ["session_id"])

    # 2. session_freezes
    op.create_table(
        "session_freezes",
        sa.Column("freeze_id", sa.Text, primary_key=True),
        sa.Column("origin_session_id", sa.Text, nullable=False),
        sa.Column("new_session_id", sa.Text, nullable=False),
        sa.Column("summary_id", sa.Text, nullable=True),
        sa.Column("trigger_ratio", sa.Float, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_sf_origin_session", "session_freezes", ["origin_session_id"])

    # 3. executions 增量
    op.add_column("executions", sa.Column("loop_id", sa.Text, nullable=True))
    op.add_column("executions", sa.Column("loop_iteration", sa.Integer, nullable=True))
    op.add_column("executions", sa.Column("dynamic_parent_id", sa.Text, nullable=True))

    # 4. sessions 增量
    op.add_column("sessions", sa.Column("archived_at", sa.DateTime, nullable=True))
    op.add_column("sessions", sa.Column("origin_session_id", sa.Text, nullable=True))
    op.add_column("sessions", sa.Column("token_count", sa.Integer, server_default="0"))


def downgrade():
    op.drop_column("sessions", "token_count")
    op.drop_column("sessions", "origin_session_id")
    op.drop_column("sessions", "archived_at")
    op.drop_column("executions", "dynamic_parent_id")
    op.drop_column("executions", "loop_iteration")
    op.drop_column("executions", "loop_id")
    op.drop_index("ix_sf_origin_session", table_name="session_freezes")
    op.drop_table("session_freezes")
    op.drop_index("ix_prs_session_id", table_name="plan_runtime_state")
    op.drop_table("plan_runtime_state")
```

### 9.6 跨 session bind_doc 继承查询

```sql
-- 新 session 启动时继承 bind：
SELECT bound_doc_id, bind_expires_at, approval_scope
FROM sessions
WHERE session_id = (
    SELECT origin_session_id FROM sessions WHERE session_id = :new_sid
) AND bind_expires_at > now();
```

---

## 10. 测试策略

### 10.1 测试分层（继承 Phase 2）

| 层 | 范围 | 环境 | 速度 |
|---|---|---|---|
| 单元 | Runtime / Compressor / AST P1P2 / Renew / 节点 schema | 内存 / mock | < 100ms |
| 集成 | Planner 升级 + Runtime 集成 + Scheduler 委托 | mock LLM + mock executor | 1-5s |
| 端到端 | 动态 DAG / while 循环 / 上下文压缩 / 续期卡片 | mock LLM + mock executor | 5-30s |
| 合约 | OpenAI function calling + LLM 多角色 schema | jsonschema | < 1s |

### 10.2 测试矩阵（按子系统）

| 子系统 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| PlanRuntime | 状态机 / loop counter / append_dynamic_nodes 校验 | append 后重新 ready 触发 | while + branch 链路 |
| DAGNode 升级 | branch/while/for schema + 校验 | Planner 输出嵌套子树 | "画图若失败则重试" |
| Planner 升级 | JSON → 嵌套 DAGNode | mock LLM 返回含 branch 的 plan | — |
| ContextCompressor | token 估算 / 触发 / freeze | 80% / 95% 双阈值 | 模拟长会话触发压缩 |
| ASTGuard P1/P2 | 命中 + 返回 report | — | — |
| bind_doc 续期 | renew() / maybe_send_renew_card | 卡片回调 → BindDocService | `/bind-doc-renew` 全链路 |
| Scheduler 委托 | Runtime 注入 / 不注入两种模式 | run_until_done 不变 | — |

### 10.3 覆盖率目标

| 模块 | 目标 |
|---|---|
| `orchestrator/runtime/plan_runtime.py` | ≥ 90% |
| `orchestrator/runtime/context_compressor.py` | ≥ 90% |
| `orchestrator/tools/ast_guard.py`（升级后）| ≥ 85%（P1/P2 提示路径）|
| `orchestrator/bind_doc_service.py`（升级后）| ≥ 85%（renew + maybe_send_renew_card）|
| `orchestrator/planner/scheduler.py`（重构后）| ≥ 75%（保持接口稳定）|
| **整体** | **≥ 85%** |

### 10.4 关键测试场景（端到端）

| # | 场景 | 验证点 |
|---|---|---|
| E1 | "如果 doc 里出现错误信息，则重试一次 read_doc" | branch 节点 + 动态追加 |
| E2 | "循环 BLAST 直到 E-value < 0.001" | while + LLM loop_eval + max_iter |
| E3 | "对每个文件做相同分析" | for + iterate_over + body 引用 item |
| E4 | 长会话（>200k token）| ContextCompressor 总结 + 冻结 + 新 session |
| E5 | 命中 requests.get | ASTGuard P1 提示 + audit |
| E6 | bind_doc 过期前 5 分钟 | 自动发续期卡片 |
| E7 | `/bind-doc-renew` | 立即续 30min |
| E8 | 嵌套子树校验失败 | Runtime.append_dynamic_nodes 抛 DAGValidationError |

### 10.5 mock 策略

- **LLM 多角色**：FakeLLMRouter 支持 `responses_by_role: dict[role, list]`
- **Runtime 状态机**：FakeRuntime 替代 PlanRuntime，让 Scheduler 测试不依赖 Runtime 内部
- **tiktoken**：用 `len(text) // 4` 作为 token 估算的 fallback

### 10.6 集成测试约束

- LLM 多角色 schema 变更需更新 jsonschema 合约测试
- Runtime 测试用 in-memory sqlite + StaticPool 共享连接
- 端到端 e2e 必须跑通 E1-E8 全部 8 个场景

### 10.7 回归保证

- Phase 2 全部 147 测试**必须**继续通过（除 Runtime 接入导致的小调整）
- Scheduler 重构保留 `run_until_done()` 接口签名
- Orchestrator `process_phase2` 仍可工作（runtime=None 时走老路径）

---

## 11. Phase 3 不做（明确边界）

| 项 | 推迟到 | 原因 |
|---|---|---|
| while/for 嵌套（body 内再嵌套 while/for）| Phase 4 | 状态机复杂度翻倍；Phase 3 仅支持单层 |
| break/continue 显式控制流 | Phase 4 | 需要 LLM 生成控制流标记；语义模糊 |
| 跨 Plan 共享 Runtime | Phase 5 | 单 Plan 独立 Runtime 已够；联邦场景下重做 |
| Runtime 状态序列化压缩 | Phase 5 | Phase 3 size<10MB 可接受；gRPC 切换时再优化 |
| 路径 symlink 检测 | Phase 4 | 静态 AST 处理复杂；运行时 chroot 替代 |
| LLM 总结失真检测 | Phase 4 | Phase 3 接受误总结风险；Phase 4 加 RAG 引用 |
| 选择性遗忘（用户可标记删除某些历史）| Phase 4 | 与"诚实记录"原则冲突，需产品决策 |
| while / for 节点手动指定 max_iterations | Phase 3 做 | 不在此列（已有 config.max_iterations） |
| Runtime 异常自动重试 | Phase 4 | Phase 3 仅记录 + 节点 FAILED；自动重试需产品决策 |
| 跨 session bind_doc 自动延长 | Phase 3 做 | 不在此列（§6.4 已继承） |
| LLM 决策回退为"恒 true" | Phase 4 | Phase 3 异常退化为"false"保守策略 |
| while/for 内嵌套 branch | Phase 3 做 | 不在此列（§7.7 允许） |
| IM 卡片"续期 +10 分钟"自定义时长 | Phase 4 | 固定 30min 简单；定制化需产品决策 |
| 多用户合签续期 | Phase 4 | 单人场景已覆盖 |
| 自动续期（用户预先授权"始终续"）| Phase 5 | 安全风险高；需产品决策 |
| session 合并（多 session 合并成一个）| Phase 4 | 反向操作；冻结合并需求少 |
| 续期卡片定时器 | Phase 3 做 | 不在此列（§6.6 BackgroundTaskRunner） |
| Kernel 状态序列化 | Phase 5 评估 | 用户已选"不做"；spec §1.4 已明确 |
| GPU 节点（AlphaFold 推理）| Phase 5 | 不在 Phase 3 范围 |
| 输出 DLP 扫描 | Phase 5 | 与 Phase 3 异步；需产品决策 |

---

## 12. 风险与决策

### 12.1 风险登记

| 风险 | 等级 | 缓解 | 触发升级条件 |
|---|---|---|---|
| **LLM 总结失真导致后续判断错** | 高 | 保留 summary_id + 最近 5 条不压缩；audit_logs 可追溯原文 | 真实场景总结失真 >10% 反馈 |
| **while 死循环（LLM 持续返回"continue"）** | 高 | max_iterations=10 强上限；audit(action=loop_max_iter) | 用户反馈 max=10 太短/太长 |
| **for 节点误把大列表迭代（>1000 items）** | 中 | max_iterations=100；超过 audit warning | 用户反馈频繁触发警告 |
| **动态追加破坏 DAG 不变量** | 中 | Runtime.append_dynamic_nodes 必走 validate_dag | 用户反馈出现悬空引用 |
| **上下文压缩时机过晚** | 中 | LLM 调用前必须 estimate；超 95% 强制 freeze | 用户反馈超限时报错 |
| **新 session bind_doc 继承带来意外授权** | 中 | 仅当原 bind 未过期才继承；过期则新 session 不带 bind | 用户反馈"我以为已经过期了" |
| **续期卡片被忽略导致过期** | 低 | 过期前 5 分钟 + 1 分钟两次提醒 | 用户反馈没看到卡片 |
| **续期被滥用为永久授权** | 低 | 每次 30min + 全程审计 + 单人单 session 限 | audit 出现异常续期频率 |
| **P1/P2 提示噪音淹没有用信息** | 低 | 仅首次命中发卡片；后续 audit 仅记录 | 用户反馈提示太多 |
| **AST P1 误报（requests 在沙箱内合法）** | 低 | 仅提示，不阻断；用户可忽略 | 误报率 >5% |
| **Runtime state_json 膨胀（>10MB）** | 低 | 单 Plan 上限 200 节点；节点 size 不大 | state_json>5MB |
| **嵌套子树校验成本（>100 节点）** | 低 | validate_dag 是 DFS；O(N+E) 线性 | Plan 节点数 >500 |
| **Scheduler 重构回归 Phase 2 测试** | 中 | 保留接口；Scheduler 单测双模式（带/不带 Runtime）| 任何 Phase 2 测试失败 |
| **LLM 角色 token 预算分裂** | 中 | 每个 role 独立 primary/fallback；LLMRouter 扩展 | 角色 >5 个时管理成本高 |
| **冻结 session 后用户困惑** | 低 | IM 明确提示"上次会话已归档"+ 提供 /resume 旧 session 命令 | 用户频繁反馈困惑 |

### 12.2 关键决策（ADR 候选）

| 决策 | 备选 | Phase 3 选择 | 理由 |
|---|---|---|---|
| Runtime 抽象粒度 | 原地增量 / 抽 PlanRuntime / 响应式流 | 抽 PlanRuntime | §1.4 已确认；边界清晰 |
| DAG 动态化触发 | LLM 决策 / 表达式解析 | LLM 决策 | §1.4 已确认；表达力强 |
| 循环上限 while | 5 / 10 / 20 | 10 | 平衡表达力与失控风险 |
| 循环上限 for | 50 / 100 / 200 | 100 | for 迭代通常是已知集合；上限可更高 |
| 上下文压缩阈值 | 70% / 80% / 90% | 80% 总结 + 95% 冻结 | 两层阈值给 LLM 留 buffer |
| P1/P2 处理 | 拒绝 / 仅提示 / 自动改写 | 仅提示 | 不误伤合法用例 |
| bind_doc 续期模式 | 仅指令 / 仅卡片 / 双通道 | 双通道 | 灵活 + 主动提醒 |
| 冻结时 bind 继承 | 不继承 / 继承 / 条件继承 | 继承 | 用户零打扰；剩余时间足以用完 |
| AST 分级 | 仅 P0 / P0+P1 / P0+P1+P2 | 全三级 | 兼容性最好；按需启用 P1/P2 |
| 嵌套子树校验 | 运行时 / 静态 | 静态 validate_dag | 早失败；低成本 |
| Runtime 持久化 | 内存 / DB / Redis | DB（plan_runtime_state）| 与 Phase 1/2 一致；Phase 5 切 Redis |
| LLM 多角色 | 单模型多 prompt / 多 model 多 role | 多 role（同一 model）| 表达清晰；成本可控 |
| Session 冻结后是否可恢复 | 仅冻结可恢复 / 仅恢复可恢复 | 默认 frozen | 与"安全归档"原则一致 |
| while LLM 决策回退策略 | 退化为 true / 退化为 false / 抛错 | 退化为 false（保守退出）| 避免资源耗尽 |
| Scheduler 重构兼容性 | 破坏式重构 / 兼容双模式 | 兼容双模式（runtime=None 时 Phase 2 路径）| 0 回归 |

### 12.3 关键依赖

- `tiktoken`（token 估算）：Phase 3 新增
- `jsonschema`（合约测试）：Phase 2 已有，Phase 3 扩展多角色
- DB schema 迁移脚本（alembic 0003）：Phase 2 模式
- BackgroundTaskRunner：Phase 3 内部新增简单协程轮询；Phase 5 替换独立 worker

### 12.4 与其他 Phase 的衔接

| 与 Phase X 的衔接 | 说明 |
|---|---|
| **Phase 1** | 主存模型 + IM/Doc/Base 适配器 + bind_doc 机制全部复用；sessions 表扩字段 |
| **Phase 2** | Scheduler 重构兼容；ExecutorClient / KernelPool / ToolHandler 不变；LarkCLI 调用不变 |
| **Phase 4** | 富文本块 / 模板市场 / 领域工具 等可直接基于 Phase 3 Runtime 注册新节点类型 |
| **Phase 5** | PlanRuntime 是 gRPC 拆分的目标之一（与 Executor / Approval 一起拆到独立进程）|

---

## 13. ADR 清单（实施前必补）

按 Phase 1/2 的惯例，Phase 3 实施前必须补 5 个 ADR 文档到 `docs/superpowers/specs/adrs/`：

| ADR # | 标题 | 备选 | Phase 3 选择 |
|---|---|---|---|
| **ADR-001** | Runtime 抽象抽离 vs 原地增量 | 原地增量 / 抽 PlanRuntime / 响应式流 | 抽 PlanRuntime |
| **ADR-002** | DAG 动态化触发机制 | LLM 决策 / 表达式解析 | LLM 决策 |
| **ADR-003** | 上下文压缩双阈值策略 | 70%/90% 单阈值 / 80%+95% 双阈值 | 80%+95% 双阈值 |
| **ADR-004** | AST P1/P2 处理策略 | 拒绝 / 仅提示 / 自动改写 | 仅提示 |
| **ADR-005** | bind_doc 冻结时是否继承 | 不继承 / 继承 / 条件继承 | 继承 |

每个 ADR 应包含：
- **背景**（为什么要决策）
- **选项**（2-3 个备选）
- **选择**（Phase 3 选择）
- **后果**（trade-off + 风险）
- **回滚条件**（什么情况下重新评估）

---

## 14. 后续动作

**Phase 3 实施前**：

1. ✅ **写完本 spec**：13 章全部展开（范围、架构、Runtime、Compressor、AST、bind_doc、Planner、Scheduler、Schema、测试、不做、风险、ADR）
2. ⏭️ **用户评审 spec**：用户确认 13 章无误
3. ⏭️ **写 5 个 ADR 文档**到 `docs/superpowers/specs/adrs/`
4. ⏭️ **调用 writing-plans skill**：把 spec 拆成 22-28 个 Task 的实施计划
5. ⏭️ **用户评审 plan**
6. ⏭️ **Subagent 实施**（按 Phase 1/2 节奏）

**Phase 3 验收后启动 Phase 4**：

| Phase 4 内容 | spec 已列 |
|---|---|
| 富文本块 / 条件块 / 用户自定义模板 | spec §11 |
| 工具热加载 + 工具市场 | spec §11 |
| 领域工具（BLAST / AlphaFold 完整实现） | spec §11 |
| 文件夹批量上传 / 分享权限管理 | spec §11 |
| 群聊上下文级审批 / 多用户合签 | spec §11 |

**Phase 3 阶段门**（gate）：

| 阶段门 | 标准 |
|---|---|
| 设计门 | spec 13 章用户确认 + 5 ADR 文档就位 |
| 实施门 | plan 22-28 Task 用户评审通过 |
| 测试门 | 147 + Phase3 新增测试全部通过；E1-E8 场景覆盖 |
| 部署门 | PostgreSQL `alembic upgrade head` 通过；Docker 集成测试通过 |
| 演示门 | 真实 LLM API + bind_doc 续期 + while 循环各跑通一次 |