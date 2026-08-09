# 飞书科研闭环 Agent — 统一设计稿

> 日期：2026-08-08
> 状态：Phase 1 设计定稿
> 目标：统一系统主存、权限边界、失败恢复与 Phase 1 范围

---

## 1. 设计目标

这个系统的目标不是"把飞书当数据库"，而是让用户**在飞书里完成研究协作**：

1. 用户在飞书 IM 里下达自然语言指令
2. Agent 解析意图并编排任务
3. 任务在受控计算环境中执行（Phase 2+）
4. 结果以文本、表格、图片、附件形式回写到飞书
5. 用户继续在飞书里追问、修订、确认下一步

核心体验仍然是"飞书即工作台"，但为了让系统可恢复、可审计、可扩展，**系统状态必须有单一事实源**。

---

## 2. 设计原则

### 2.1 单一事实源

**PostgreSQL 是系统唯一主存（source of truth）。**

- `sessions / tasks / artifacts / doc_writes / audit_logs` 全部以 PostgreSQL 为准
- 飞书 Base 只作为**用户可见状态投影**（异步写，失败不影响主事务）
- 飞书 Doc 是**叙事与结果载体**
- 飞书 Drive 是**文件存储与分享载体**

这样做的原因：

- Base 更适合展示，不适合作为高一致性事务主存
- Doc / Drive 有很强的协作价值，但不适合承载执行状态机
- PostgreSQL 更适合做事务、恢复、重试、审计与幂等控制

一句话定义：

> **PostgreSQL 管系统真相，飞书承载用户界面与协作结果。**

### 2.2 默认安全

- 所有外部输入默认不可信
- 所有副作用操作默认需要显式授权
- 所有长任务都必须可取消、可重试、可审计
- 所有结果写回都必须有可恢复现场

### 2.3 先可交付，再扩展

Phase 1 只解决一条最短路径：

> IM 收到消息 → LLM 生成回复 → 用户确认 → 追加到绑定文档

不在 Phase 1 引入代码执行、Jupyter 内核恢复、动态 DAG、领域工具插件。

Phase 2~5 的扩展性设计已归档到 [`2026-08-08-feishu-research-agent-phase2-5-archive.md`](./2026-08-08-feishu-research-agent-phase2-5-archive.md)。

---

## 3. 系统边界

### 3.1 用户可见层（Feishu Surface）

| 载体 | 角色 | 是否主存 |
|---|---|---|
| IM | 指令输入、通知、审批 | 否 |
| Doc / Wiki | 结果呈现、研究叙事 | 否 |
| Base | 状态投影、任务看板、只读追踪 | 否 |
| Drive | 文件分发、下载、分享 | 否 |

### 3.2 系统控制层

| 模块 | 角色 |
|---|---|
| Gateway | Webhook 接入、签名校验、限流、幂等去重 |
| Orchestrator | 会话管理、意图解析、计划生成、审批编排 |
| Persistence | PostgreSQL 仓储、审计与幂等 |
| Feishu Adapter | IM / Doc / Base / Drive 薄封装 |

---

## 4. 总体架构

```text
飞书 IM / Doc / Base / Drive
          │
          ▼
Gateway (Webhook / Signature / Rate Limit / Idempotency)
          │
          ▼
Orchestrator
  ├─ Session Service
  ├─ Bind-Doc Service
  ├─ LLM Router
  ├─ Task Service
  ├─ Doc-Write Service
  └─ Feishu Projection Writer
          │
          ├──────────────► PostgreSQL（唯一主存）
          │                  ├─ sessions
          │                  ├─ tasks
          │                  ├─ artifacts
          │                  ├─ doc_writes
          │                  └─ audit_logs
          │
          └──────────────► Feishu Adapter
                              ├─ IM Adapter
                              ├─ Doc Adapter
                              ├─ Base Projection Adapter
                              └─ Drive Adapter (Phase 2+)
```

关键约束：

1. **任何任务状态变更先写 PostgreSQL，再投影到 Base**
2. **任何文档写入都必须有对应的 `doc_writes` 记录**
3. **任何副作用操作都必须绑定发起人和审批记录**

---

## 5. 权限与审批模型

这是本次重写里最重要的收口点。

### 5.1 工具风险分级

| 级别 | 示例 | 默认策略 |
|---|---|---|
| L0 只读 | `read_doc`, `read_base`, `list_drive` | 自动允许 |
| L1 纯计算 | `summarize_text`, `classify_intent`, `run_python` | 会话内允许，受沙箱限制（Phase 2+） |
| L2 副作用 | `write_doc`, `write_base_projection`, `send_card`, `upload_drive` | 必须有审批记录 |

### 5.2 审批规则

**统一规则：任何会改变飞书内容或外部状态的操作，都属于 L2。**

包括：

- 向文档追加内容
- 更新 Base 投影
- 上传新文件到 Drive
- 发送带确认语义的交互卡片

### 5.3 Phase 1 特例：bind-doc 前置授权

为了不牺牲可演示性，Phase 1 允许一个受控例外：

- 用户先通过 `/bind-doc <doc_id>` 显式绑定目标文档
- 绑定成功后，**同一用户、同一会话、30 分钟内**可以对该文档执行"追加纯文本回复"
- 这个能力只覆盖 `append_plain_text`
- 超出 30 分钟、跨用户、跨文档、跨群聊上下文，一律重新确认

这不是放弃审批，而是把审批前置成一次显式绑定。

### 5.4 Phase 2+ 的审批流（参考）

Phase 2 之后，对 L2 操作恢复完整的卡片审批流：

- 工具被调用前，发送 IM 交互卡片（含 nonce + 30min TTL）
- 用户点击"确认"或"拒绝" → 卡片回调进 Gateway
- 校验 `event_token` + `app_secret` HMAC 后才执行
- 拒绝 → task 标 cancelled，错误原因写 `audit_logs`

---

## 6. 单一事实源与数据模型

### 6.1 PostgreSQL 表

#### `sessions`

| 字段 | 类型 | 说明 |
|---|---|---|
| session_id | text PK | ULID |
| owner_open_id | text | 会话发起人 |
| source_chat_id | text | 来源会话（私聊 / 群聊） |
| bound_doc_id | text nullable | 当前绑定文档 |
| bind_expires_at | datetime nullable | 绑定过期时间 |
| approval_scope | jsonb | 当前授权范围 |
| status | text | active / idle / closed |
| created_at | datetime | |
| updated_at | datetime | |

#### `tasks`

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | text PK | ULID |
| session_id | text FK | 所属会话 |
| parent_task_id | text nullable | 父任务，可空 |
| message_id | text | 对应飞书消息 ID（幂等键） |
| intent | text | 意图标签 |
| status | text | pending / running / waiting_approval / success / success_with_partial_failure / failed / cancelled |
| plan_json | jsonb | 计划快照 |
| reply_text | text | LLM 回复（Phase 1） |
| error_code | text nullable | 错误码 |
| error_message | text nullable | 错误摘要 |
| created_at | datetime | |
| started_at | datetime nullable | |
| finished_at | datetime nullable | |

#### `artifacts`

| 字段 | 类型 | 说明 |
|---|---|---|
| artifact_id | text PK | ULID |
| task_id | text FK | 所属任务 |
| kind | text | text / table / image / file / code |
| storage_type | text | inline / drive |
| storage_ref | text | 文本摘要或 drive token |
| sha256 | text | 去重用 |
| status | text | pending / ready / linked / orphaned / deleted |
| created_at | datetime | |
| updated_at | datetime | |

#### `doc_writes`

| 字段 | 类型 | 说明 |
|---|---|---|
| doc_write_id | text PK | ULID |
| task_id | text FK | 对应任务 |
| doc_id | text | 目标文档 |
| requested_by | text | 发起用户 |
| approval_mode | text | bind_scope / explicit_card |
| approval_id | text nullable | 审批记录 ID |
| payload_json | jsonb | 待写入块内容 |
| anchor_block_id | text nullable | 写入锚点 |
| status | text | pending / approved / writing / success / failed / orphaned / cancelled |
| fail_reason | text nullable | 失败原因 |
| created_at | datetime | |
| updated_at | datetime | |

#### `audit_logs`

| 字段 | 类型 | 说明 |
|---|---|---|
| audit_id | text PK | ULID |
| actor_type | text | user / system / tool |
| actor_id | text | open_id 或 tool 名 |
| action | text | create_task / approve / execute / write_doc / project_base / deny / cancel / bind_doc |
| target_type | text | session / task / artifact / doc_write / drive |
| target_id | text | 目标 ID |
| detail_json | jsonb | 结构化细节 |
| created_at | datetime | |

### 6.2 幂等键

`app_id:chat_id:message_id` —— Gateway 用此去重 webhook 重投。`message_id` 也写入 `tasks.message_id`，确保任务级幂等。

### 6.3 Feishu Base 的角色

Base 不再是主存，而是只读投影：

- `tasks_view`
- `recent_artifacts_view`
- `audit_summary_view`

投影失败不会影响主事务，只会记审计并进入重试队列。

---

## 7. 核心流程

### 7.1 Phase 1 正常链路

```text
用户发 IM
  → Gateway 校验签名、限流、去重
  → Orchestrator 创建 session/task
  → LLM 生成 reply_text
  → IM 回复用户
  → 判断是否存在有效 bind-doc 授权
       → 有：创建 doc_writes 记录并写文档
       → 无：结束（不写文档）
  → Base 投影异步更新
```

### 7.2 `/bind-doc` 流程

```text
用户发 /bind-doc <doc_id>
  → Gateway 校验签名与幂等
  → Orchestrator 校验 doc_id 格式
  → SessionService 创建或更新 session
  → 记录 bound_doc_id + bind_expires_at = now + 30min
  → IM 回复绑定成功
  → 写 audit_logs(action=bind_doc)
```

### 7.3 文档写入流程

1. 在 PostgreSQL 写入 `doc_writes(status=pending)`
2. 校验授权范围（bind_doc 是否在窗口内）
3. 授权通过后更新为 `approved`
4. 调用 Doc Adapter 获取最新锚点
5. 更新为 `writing`
6. 写入成功：
   - 记录 `anchor_block_id`
   - 更新 `status=success`
   - 关联对应 `artifacts.status=linked`
7. 写入失败：
   - `status=failed`
   - 若远端已部分写入但无法确认完整性，标记 `orphaned`
   - 进入修复 / 清理队列

这次重写后的关键变化是：**不再把 orphaned 仅写成一句口头规则，而是有明确表字段与状态机。**

### 7.4 取消与重试

- 用户可发 `/cancel <task_id>`
- `waiting_approval` 状态可直接取消
- `running` 状态尝试中断执行器（Phase 2+）
- 文档写入失败默认不自动无限重试
- 只允许幂等安全的写入重试（用 `doc_write_id` 去重）

---

## 8. 安全设计

### 8.1 Gateway

- 校验飞书签名，且必须基于**原始请求体**
- 按 `app_id:chat_id:message_id` 做幂等去重
- 基于 `app_id` 和 `open_id` 做双层限流
- 限流状态持久化到 PostgreSQL（`rate_limit_buckets` 表，Phase 1 用内存 + Redis 可选）

### 8.2 LLM

- 不信任模型输出
- 模型只能生成"计划"和"候选操作"
- 真正执行前必须经过：
  - 参数校验
  - 权限校验
  - 风险分级
  - 审批检查

### 8.3 Executor（Phase 2+）

AST 检查不再被定义为"可靠拦截边界"，而是**弱检测层**。真正的安全边界是：

1. 容器隔离（Docker `--network=none --read-only`）
2. 网络白名单（按域名放行）
3. 资源限额（CPU / 内存 / PID / 进程数）
4. 进程能力裁剪（`--cap-drop=ALL --security-opt=no-new-privileges`）
5. 会话结束后内核销毁

AST 黑名单只用于：

- 提前发现明显危险调用（**保留 P0 硬阻塞**：`os.system` / `subprocess.*` / `socket.*` / `ctypes.*` / 外部数据 `pickle.loads`，命中即拒绝，task 标 failed）
- 给用户更友好的拒绝原因
- 为审计提供附加证据

P1 / P2 分级（requests / urllib / 写非 `/workspace/output` 的 `open`）在 Phase 2+ 沙箱成熟后再启用；Phase 1 仅保留 P0。

---

## 9. 适配层职责

### 9.1 Doc Adapter

只负责：

- 读取块树
- 计算插入锚点
- 插入块
- 返回 `block_id`

不负责：

- 授权判断
- 会话判断
- 任务状态管理

### 9.2 Base Projection Adapter

只负责把 PostgreSQL 状态投影到飞书 Base。

如果 Base 不可写：

- 主任务仍然算成功
- 但会新增一条 `project_base_failed` 审计

### 9.3 Drive Adapter（Phase 2+）

上传文件前必须带：

- `task_id`
- `artifact_id`
- 内容哈希
- MIME
- 大小

### 9.4 IM Adapter（Phase 1 选用 lark-cli）

Phase 1 通过 `lark-cli` 子进程封装调用飞书 API，便于本地调试与测试时 mock。后续是否切到 Feishu Python SDK 留待 Phase 2 评估。

---

## 10. Phase 1 范围定义

### 10.1 要做

- 飞书 IM Webhook 接入
- 签名校验
- 幂等去重
- Session / Task / Doc-Write 主存落库
- LLM 主备调用
- `/bind-doc` 绑定目标文档
- 纯文本回复追加到绑定文档
- Base 异步投影
- 基础审计
- 单元与集成测试（37+ 用例）

### 10.2 不做

- 任意代码执行
- Docker 沙箱
- Jupyter Kernel 恢复
- 多步 DAG
- 领域工具插件
- Drive 大文件回传
- 群聊多人协同写同一文档
- 图片 / 表格 / 富文本块写入
- 完整的 IM 卡片审批流（Phase 1 用 bind-doc 前置授权代替）

---

## 11. 关键非功能要求

| 指标 | 目标 |
|---|---|
| IM 到 IM 回复 P50 | ≤5s |
| IM 到文档追加 P95 | ≤15s（绑定文档场景） |
| 文档写入幂等性 | 同一 `doc_write_id` 不重复写入 |
| 任务可追踪性 | 每个任务都有 task / audit / doc_write 关联 |
| 审批可解释性 | 用户能看到"为什么要确认 / 为什么被拒绝" |
| 测试覆盖率 | ≥60%（仅 Phase 1 代码） |

---

## 12. 风险与决策

| 主题 | 决策 |
|---|---|
| 主存归属 | PostgreSQL 唯一主存 |
| 飞书 Base 角色 | 状态投影，不承担事务真相 |
| 文档写入权限 | L2 副作用；Phase 1 用 bind-doc 前置授权 |
| orphaned 恢复 | 通过 `doc_writes` 与 `artifacts.status` 落地 |
| AST 检查定位 | 弱检测层，**保留 P0 硬阻塞**，P1+ 在 Phase 2 引入 |
| 飞书调用方式 | Phase 1 用 lark-cli；Phase 2 评估 SDK |
| 幂等键 | `app_id:chat_id:message_id`，同时写入 `tasks.message_id` |

---

## 13. 后续动作

1. 按本设计稿执行 [`../plans/2026-08-08-feishu-research-agent-phase1.md`](../plans/2026-08-08-feishu-research-agent-phase1.md) 的 14 个 Task
2. Phase 1 验收后启动 Phase 2 设计（Docker 沙箱 / Kernel / DAG / 工具插件），参考归档文件 [`2026-08-08-feishu-research-agent-phase2-5-archive.md`](./2026-08-08-feishu-research-agent-phase2-5-archive.md)
3. 实施前补一页 ADR：
   - 为什么 PostgreSQL 是主存
   - 为什么 Base 只做投影
   - 为什么 Phase 1 采用 `/bind-doc` 前置授权
   - 为什么 AST 仅保留 P0