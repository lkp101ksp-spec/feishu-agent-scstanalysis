# 长会话记忆 设计文档（2026-09-08）

## 背景与目标

Phase 3 建了 ContextCompressor / SessionService.freeze_session / FreezeRequired，
但 Phase 3.1 接线从未做（2026-09-08 侦察归档挂起）。本 feature 正式立项
**私聊多轮记忆**：让飞书私聊闲聊具备多轮上下文，并复活压缩/冻结组件。

需求决策（用户确认）：
- 核心场景：**多轮闲聊记忆**（追问、指代），非长期事实记忆、非任务上下文延续
- 范围：**仅私聊**；群聊闲聊维持静默忽略现状
- 上下文策略：**全量历史 + ≥80% 压缩 + ≥95% 冻结开新会话**（复活 Phase 3 组件）
- 用户控制：新增 **/clear** 命令手动重置会话记忆

接入架构（用户选定方案甲）：MessageStore 仓储 + ChatMemory 编排服务 + 主路径显式接入。

## 组件清单

### 新增

1. **`messages` 表**（`persistence/models.py` +MessageRow）
   - `message_id` String ULID 主键
   - `session_id` String 索引（sessions.session_id 逻辑外键，不加物理约束，与项目风格一致）
   - `role` String（user / assistant / system——system 仅压缩摘要行）
   - `content` Text
   - `created_at` DateTime 默认 utcnow
2. **迁移** `migrations/versions/0003_messages.py`：建表 + `ix_messages_session_id` 索引
3. **`persistence/repositories/message_repo.py`** MessageRepo
   - `append(session_id, role, content) -> MessageRow`（flush，不 commit，与项目惯例一致）
   - `list_all(session_id) -> list[MessageRow]`（created_at 升序）
   - `delete_many(message_ids) -> None`（压缩回写用）
4. **`orchestrator/chat_memory.py`** ChatMemory（编排服务，本 feature 核心）
   - `prepare(session_id, chat_id) -> (history, 生效 session_id, 是否冻结)`：
     读库 → ContextCompressor.maybe_compress → 压缩则 replace_all 回写；
     捕获 FreezeRequired → 内部编排 freeze（见时序节）
   - `append_turn(session_id, user_text, reply_text)`：双写 user/assistant
   - `clear(session_id) -> str`：/clear 入口，复用 freeze_session（summary 空、ratio 0）
5. **`config/settings.py` 零新增**：`context_token_budget`（L56，默认 200_000）与
   `context_compress_trigger_ratio`/`context_freeze_trigger_ratio`/`context_preserve_recent_n`
   （L65-67）四个字段 Phase 2/3 已存在，直接复用；真机验证时 env 覆盖即可

### 改动

6. **`orchestrator/app.py`** process()：
   - 指令路由新增 `/clear`
   - 私聊闲聊路径接入 ChatMemory（见数据流节）
   - process_phase3 docstring 更新（Phase 3.1 由本 feature 正式落地）
7. **`gateway/runtime.py`**：装配 ChatMemory（compressor + message_repo + session_service
   + audit_repo + llm），挂 `orch.chat_memory`，沿用现有服务挂接模式
8. **`orchestrator/runtime/context_compressor.py`**：抽 `summarize_only(messages) -> str`
   （freeze 前强制摘要复用压缩 prompt；LLM 失败由调用方降级）

## 数据流

### 正常路径（私聊非指令消息）

```
incoming → 指令路由（/clear 等优先）→ 群聊门控（不变）→ 意图闸（不变）
 → session_service.get_or_create(owner, chat) → task 创建
 → history = chat_memory.prepare_history(session_id)
      ├ message_repo.list_all
      ├ compressor.maybe_compress（≥80% 触发 LLM 总结）
      └ 发生压缩 → 回写：delete_many(旧行) + append(system 摘要行)
 → reply = llm.chat([system SYSTEM_PROMPT] + history + [user 当前])
 → im.reply（先回复，不变）
 → chat_memory.append_turn(session_id, 当前, reply)
 → 绑定文档写文档逻辑原样不动
```

### freeze 时序（maybe_compress 抛 FreezeRequired）

```
ChatMemory 捕获 FreezeRequired：
 1. summary = compressor.summarize_only(全量历史)   # LLM 失败 → 空串降级
 2. new_sid = session_service.freeze_session(
        session_id=旧, summary=summary, trigger_ratio=当前 ratio)
    （旧行 archived、新行继承 bind/approval_scope/origin——已修复并真库钉死）
 3. summary 非空 → message_repo.append(new_sid, "system", f"[已压缩] {summary}")
 4. im.reply "[系统] 上下文已满，已开启新会话（历史摘要已继承）"
 5. 返回 (new_sid, [摘要行])，当前消息在新会话继续走正常路径
    → 返回状态 "frozen_and_continued"
```

### /clear

`/clear` → chat_memory.clear(session_id) → freeze_session(summary="", ratio=0)
→ 回复 "[成功] 已开启新会话，历史已清空"。群聊可用（会话以 (owner, chat) 为粒度，
只清自己的）。

## 错误处理（全部降级，不阻断聊天）

| 故障点 | 降级行为 |
|---|---|
| messages 表读/写异常 | log warning，当轮按无记忆处理（空 history） |
| 压缩 LLM 调用异常 | 本轮不压缩继续（未达 freeze 阈值时） |
| freeze 前摘要 LLM 异常 | 空摘要冻结（新会话从零开始） |
| freeze 后重跑 LLM 失败 | 走现有 LLMCallError 路径（标 task failed + 回复错误） |
| FreezeRequired 里拿不到旧 session 行 | assert 即崩（编程错误，与 freeze_session 契约一致） |

## 测试策略（TDD 红→绿）

1. MessageRepo 真库测试（sqlite StaticPool 既有模式）：append/list_all 升序/delete_many
2. 迁移测试：`messages` 表与索引存在（test_phase3_orm 同款）
3. ChatMemory 单测（mock 边界）：历史装配、压缩回写（删旧+插摘要）、
   freeze 编排全链路（捕获→摘要→freeze→通知→续跑）、摘要失败降级、/clear
4. process() 集成（mock LLM）：多轮历史递增验证、FreezeRequired 续跑路径、
   /clear 路由
5. 全量回归 + ruff + mypy 双平台门禁
6. **真机验收**：`.env` 临时加 `CONTEXT_TOKEN_BUDGET=200` +
   `CONTEXT_FREEZE_TRIGGER_RATIO=0.5` → 重启 ws_client → 私聊数轮依次触发
   压缩与冻结 → ws_client 日志 + pg `sessions`/`messages`/`session_freezes`
   行核对 → 调回阈值重启还原

## 范围外（YAGNI）

群聊记忆、长期事实记忆、coding/research 链路历史、历史管理界面/单条删除、
向量检索、记忆跨设备同步。

## 风险与注意

- **token 成本**：全量历史每轮全量发送，成本随会话长度增长——由压缩/冻结兜底，
  这正是组件复活的初衷
- **写库时机**：回复先于落库（im.reply 在前），ws_client 崩溃最多丢一轮记忆，
  可接受
- **并发**：同一用户并发消息在 gateway 层已有幂等键串行化（消息幂等键 +
  卡片双层），messages 追加无竞态面
- **pg/sqlite 方言**：DateTime 读回 naive 的坑已在 freeze_session 修复中处理，
  新代码涉时间比较时沿用同款补 UTC 模式
