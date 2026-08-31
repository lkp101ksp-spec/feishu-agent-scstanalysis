# Phase 18：评论协作补全 — 设计文档

- 日期：2026-09-01
- 状态：已定稿（真机验收推迟，与 Phase 19 合并验收）
- 来源：ROADMAP Phase 18（Phase 9/11 不做项）

## 1. 背景与现状

评论子系统现状（Phase 7/8/9/11 已落地）：

| 组件 | 现状 | 缺口 |
|---|---|---|
| `CommentClient.list_comments` | 单页拉取（默认 page_size） | 无分页循环，评论 >50 条丢失 |
| `CommentSyncService.sync` | 拉取 → 展平 → 幂等 upsert | 远端已删评论本地残留（无对账） |
| `CommentEventService` | comment_add_v1 → 防循环 → 绑定过滤 → sync+notify | 无 LLM 问答能力 |
| `POST /webhook/lark` | 仅 IM 事件（run_im_pipeline） | 无 challenge 应答、无评论事件分流（ws 单通道） |

## 2. 目标

1. **T1 分页**：list_comments 按 page_token 循环拉全量（上限保护）。
2. **T2 删除对账**：sync 后本地 vs 远端 diff，远端消失的评论本地物理删除。
3. **T3 评论问答**：评论正文以 `/ask ` 开头（或含 `@agent`）→ 基于绑定文档上下文 LLM 作答 → reply_comment 回写评论。
4. **T4 webhook 容灾通道**：`/webhook/lark` 支持 url_verification challenge + comment_add_v1 事件分流到 CommentEventService（ws 断连时兜底；飞书后台订阅配置需人工，代码就绪即可）。

## 3. 方案

### 3.1 T1 分页（feishu_adapter/comment_client.py）

- `list_comments` 循环：`page_token` 透传 queries，`has_more` 为假或达 `max_pages=20` 上限即停。
- 返回结构不变（扁平 dict 列表），分页细节内聚在 client。

### 3.2 T2 删除对账（comment_sync_service.py）

- sync 收集本次远端全部 id（root comment_id + reply reply_id）为 `remote_ids`。
- `CommentRepo.delete_missing(doc_id, keep_ids)`：删除该 doc 下不在 keep 集合内的行（快照语义：本地即远端镜像）。
- 返回 dict 增加 `deleted` 计数。
- 编辑场景天然覆盖：upsert 已更新 text/resolved。

### 3.3 T3 评论问答（comment_event_service.py + ws_client.py）

链路（复用既有事件管线，ADR-0033 线程隔离不变）：

```
comment_add_v1 → ws/webhook → CommentEventService.handle(comment_id)
  → sync（幂等，含 T2 对账）
  → notify_new_pending（既有）
  → 问答回执（新增）：评论正文命中 /ask 前缀（或含 @agent）
      → CommentNotifyRepo.has(comment_id) 幂等判重（事件重发不重复作答）
      → doc_adapter.get_block_tree(bound_doc) 提取纯文本（截断 8000 字符）
      → llm.call(role="comment_qa", prompt=文档上下文+问题)
      → comment_client.reply_comment 回写；失败仅 warning
```

- `handle()` 签名增加可选 `comment_id: str = ""`（webhook/ws 均传入）。
- 触发词：`/ask ` 前缀为主（文本可靠）；`@agent` 子串为辅（mention 元素文本化时兜底）。
- 防循环：bot 自身 reply 触发的 comment_add 已被 `ignored_bot_self` 拦截（既有）。
- 问答上下文超长截断（8000 字符），超长尾部加 `（文档过长，已截断）`。
- CommentEventService 新增可选依赖：`doc_adapter` / `llm` / `qa_reply_client`（均为 None 时禁用问答，不回归现行为）。
- 问答回执幂等复用 CommentNotifyRow（comment_id 主键，与通知去重同一张表——语义都是"该评论已产生过一次外发动作"）。

### 3.4 T4 webhook 通道（gateway/app.py）

`POST /webhook/lark` 验签后按 body 结构分流：

1. `body.type == "url_verification"` → 直接返回 `{"challenge": body["challenge"]}`（飞书事件订阅首次配置校验）。
2. `header.event_type == "drive.notice.comment_add_v1"` → 从 `event.notice_meta` 提取 file_token / operator_open_id，`event.comment_id` 一并传入 `ctx.comment_event_service.handle`；service 未装配返回 503。
3. 其余 → 现有 run_im_pipeline（行为不变）。

- AppContext 增挂 `comment_event_service`（create_app 参数 + ws 进程注入）。
- 评论事件处理不进 run_im_pipeline 的幂等表（sync/notify/问答回执各自幂等，且 event_id 维度去重暂不做——重发场景已被三层幂等覆盖）。

## 4. 不做项

- @mention 富文本元素级解析（elements.type == "mention_user" 结构依赖真机抓包，文本子串兜底足够）。
- webhook 事件订阅的飞书后台配置（需人工在开放平台操作）。
- 评论编辑独立事件（飞书无 comment_updated 推送，编辑靠 sync upsert 覆盖）。
- event_id 全局幂等表（现有三层幂等已覆盖重发风险）。

## 5. 测试计划

单测/集成：
- [ ] CommentClient 分页：mock 两页 has_more/page_token，断言聚合且不多拉
- [ ] CommentClient 分页上限：构造恒 has_more，断言 max_pages 停止
- [ ] sync 对账：预置本地孤儿行 → sync 后被删，返回 deleted 计数
- [ ] sync 对账回归：正常 upsert 行为不变
- [ ] 事件问答：/ask 触发 → llm 被调 → reply 回写；非 /ask 不触发
- [ ] 问答幂等：同一 comment 二次 handle 不重复作答（notify_log 判重）
- [ ] 问答降级：doc_adapter/llm 为 None 不触发；reply 失败不影响 handle 返回
- [ ] webhook：url_verification 返回 challenge
- [ ] webhook：comment 事件分流到 comment_event_service；未装配 503
- [ ] webhook：签名无效 401；IM 事件仍走原管线

## 6. 风险

- reply_comment 写回内容为纯文本（富文本 markdown 不渲染，可接受）。
- 问答 LLM 延迟发生在 ws 回调线程（同步调用，阻塞该事件分发；lark SDK ws 单线程分发，期间 IM 消息延迟——延迟可控：单次 LLM 调用 timeout 由 LLMRouter 配置约束）。
- 对账物理删除会丢失 processed_at 历史（已删评论的动作重放风险随之消失，可接受）。
