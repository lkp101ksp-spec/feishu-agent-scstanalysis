# Phase 11 设计：评论闭环补完

- 日期：2026-08-28
- 状态：设计已确认（方向/架构/写回深度三项用户拍板）
- 前置：Phase 10 已收尾（564 passed + pg 6 + ruff 0 error；真租户联调全链路打通，含 12 条市场指令 IM 路由）

## 1. 范围与目标

### 1.1 Phase 11 主题

把「导师在文档里批评论 → agent 感知 → owner 应用修改 → agent 回评论区回执」这条闭环真正跑通。Phase 7-9 建立了评论的拉取/同步/动作/通知四层服务，但存在三个断点：评论客户端从未真实可用（httpx GET 调官方 POST 接口 + 需手工维护静态 token）、自动同步在 ws_client 生产模式下不启动（startup 钩子无 ASGI server 触发）、agent 无法在评论区留下任何痕迹（ADR-0015 有意留白）。

### 1.2 范围（4 板块）

| # | 板块 | 交付 |
|---|------|------|
| ① | CommentClient SDK 化 | httpx 直连 → lark-oapi SDK（tenant token 自动刷新），修正 GET→POST 偏差，删除 `FEISHU_API_BASE_URL/TOKEN` 依赖 |
| ② | 评论事件接收 | 订阅 `drive.notice.comment_add_v1`，ws 长连接收事件 → 触发该 doc 的 sync + IM 通知（与轮询共用幂等逻辑） |
| ③ | 轮询兜底接线 | 修复 auto_sync_worker 在 ws_client 模式不启动的缺陷（独立 session + 守护线程），作为事件丢失补拉通道 |
| ④ | 处理回执写回 | `/comment-apply` 成功后对已处理评论回写「已按评论修改（版本 N）」回执 |

### 1.3 关键约束

- 单机单进程（分布式锁仍顺延，ADR-0031 负面条款延续）
- 评论事件官方仅支持 WebSocket 模式——与现有 ws_client 长连接天然契合，不引入 webhook HTTP 通道
- 回执为固定文案模板，不经过 LLM（内容可控可预测）
- 既有 564 默认层测试零回归

### 1.4 关键决策（用户已批）

| 决策点 | 选择 |
|--------|------|
| 大方向 | 评论闭环补完（对比科研工具/工程化/执行层增强） |
| 同步架构 | 事件为主 + 轮询兜底（双通道） |
| 写回深度 | 处理回执（不做 LLM 评论问答） |

### 1.5 不做清单

- LLM 评论问答（评论 @bot 提问生成回复）——幻觉风险 + 防循环复杂度，推迟
- 评论删除/编辑事件（仅订阅 add）
- 评论分页拉取（ADR-0019 推迟项；事件化后单次全量拉取量小，价值进一步降低）
- webhook HTTP 模式、分布式轮询锁、K8s 多实例
- 用户身份（user_access_token）订阅与 per-file subscribe API——应用身份为租户级覆盖，无需逐文档订阅

## 2. 总体架构

```
飞书文档评论区                     飞书开放平台
   │  导师添加评论                     │
   ▼                                ▼
┌─────────────────┐   ws 长连接   ┌──────────────────────────┐
│ drive.notice.    │ ───────────▶ │ ws_client dispatcher      │
│ comment_add_v1   │              │  on_doc_comment(ev)       │
└─────────────────┘              └───────────┬──────────────┘
                                             │ ①过滤 bot 自身
                                             │ ②查绑定 session
                                             ▼
                                  ┌──────────────────────────┐
                                  │ comment_event_service     │
                                  │  sync(doc_id) ← 幂等 upsert│
                                  │  notify_new_pending       │ ──▶ IM 通知 owner
                                  └───────────┬──────────────┘
                                              │ 共用同一套服务（独立 DB session）
   兜底通道（每 300s）                          │
┌─────────────────────┐                       │
│ auto_sync_worker     │ ── tick() ───────────┤
│ (守护线程, 修复接线) │                       ▼
└─────────────────────┘          ┌──────────────────────────┐
                                 │ owner: /comment-apply      │
                                 │  CommentActionService      │
                                 │  改模板 + 版本 bump         │
                                 │  + 新增: 回评论回执 ④       │ ──▶ 文档评论区
                                 └──────────────────────────┘
```

双通道安全性论证：`comment_repo` 按 comment_id 幂等 upsert（ADR-0019）+ `comment_notify_log` 持久去重（ADR-0025），事件与轮询并发触发同一 doc 的 sync/notify 不会产生重复落库或重复 IM 推送——这是选择「事件+轮询」而不担心竞争的架构前提。

## 3. 板块① CommentClient SDK 化

### 3.1 现状与问题

`feishu_adapter/comment_client.py`（36 行）：`httpx.get({base_url}/docx/v1/documents/{doc_id}/comments)` + 静态 Bearer token。三问题：官方列表接口为 POST 而 实现 为 GET（该链路从未真实跑通，测试全 mock）；token 需手工维护会过期；未随 ADR-0031 迁移 SDK（IM/Doc 已迁，评论漏网）。

### 3.2 目标形态

- `CommentClient(sdk_client=..., rate_limiter=...)`：走 lark-oapi SDK `drive` 命名空间（`fileComment` / `fileCommentReply`），tenant token 自动刷新
- 保留 `RateLimiter(3/s)` 注入（自定义限流叠加 SDK 默认限流，双保险）
- `list_comments(doc_id)` / `list_block_comments(doc_id, block_id)` 签名不变（下游五个服务零改动）
- 新增 `reply_comment(file_token, comment_id, text)`（板块④用，纯文本 element 封装）
- `gateway/runtime.py`：删除 `api_base/api_token` 读取与条件装配开关——评论子系统改为无条件组装（凭据即 SDK 的 app_id/secret）

### 3.3 交付物

改造 `comment_client.py` + `runtime.py` 装配段 + `.env.example` 删除 `FEISHU_API_*` 两行 + 相关单测改造（原 mock httpx 改 mock SDK client）。

## 4. 板块② 评论事件接收

### 4.1 事件与订阅

- 事件：`drive.notice.comment_add_v1`（新增评论 add_comment / 新增回复 add_reply，`notice_type` 区分）
- 订阅方式：开发者后台「事件与回调」添加事件（长连接模式）；应用身份为租户级覆盖，无需 per-file subscribe
- payload 关键字段：`file_token`、`file_type`（docx/sheet/file）、`comment_id`、`reply_id?`、`operator_id.open_id`、`is_whole`；**payload 不含评论正文**——正文经 sync 全量拉取获得（事件仅作触发器）

### 4.2 处理链（comment_event_service）

ws_client dispatcher 新增 `on_doc_comment` 回调（SDK 线程内执行）：

1. **防循环**：`operator_id.open_id == bot open_id` → 直接 return（板块④回执写回会触发新事件）
2. **绑定过滤**：`file_token` 无活跃绑定 session（`session_repo` 按 `bind_doc_id` 匹配且未过期）→ return（未绑定文档的评论不处理）
3. **触发同步**：对该 doc 执行 `comment_sync_service.sync(doc_id)` + `comment_notify_service.notify_new_pending(...)`
4. 异常吃掉记日志（单事件失败不影响长连接）

### 4.3 线程与 session 隔离

事件回调在 SDK 的 ws 线程执行，**严禁共享主管线 Session**——照搬续期扫描器模式（ADR-0031 后续实践）：`build_runtime` 用独立 `event_session` 组装 `comment_event_service`（SessionRepo + CommentSyncService + CommentNotifyService），挂 `Runtime.comment_event_service` 供 ws_client 取用。

### 4.4 交付物

`ws_client.py` dispatcher 注册 + `runtime.py` 组装 comment_event_service + payload 归一化函数 + 单测（payload 解析 / bot 过滤 / 无绑定短路 / 异常隔离）。

## 5. 板块③ 轮询兜底接线

### 5.1 缺陷与修复

现状：`auto_sync_worker.start_async()` 仅挂 FastAPI `on_event("startup")`（gateway/app.py）——uvicorn 模式生效，ws_client 模式无 ASGI server，**生产实际不运转**。

修复（对齐 renew scanner 模式）：

- `ws_client.py` 新增 `start_auto_sync_scanner(rt)`：守护线程循环 `sleep(interval) → asyncio.run(worker.tick())`（tick 为单轮方法，天然适配）
- `runtime.py`：auto_sync_worker 改用独立 `scan_session` 组装（原与主管线共用主 session，跨线程不安全）
- uvicorn 入口保留 startup 钩子，两入口二选一不并存（注释说明，防双轮询）
- 日志：启动时打 `auto comment sync scanner started (interval=Ns)`

### 5.2 交付物

接线 + 独立 session 组装 + 单测（tick 周期调用 / service None 时 noop / 双入口说明性测试）。

## 6. 板块④ 处理回执写回

### 6.1 行为

`CommentActionService.apply` 成功处理一条评论（模板已改 + 版本 bump + mark_processed）后，对该 `comment_id` 调 `CommentClient.reply_comment`，固定文案：

```
已按此评论完成修改：模板 {template_id} 已更新至版本 {version}。
```

- 回执失败（网络/权限）不阻断 apply 主流程，逐条记 warning，结果 dict 的 failed 计数语义不变
- apply 幂等（processed 不重处理）保证回执不重发
- 写回权限：`docs:document.comment:create`（见 §8 权限清单）

### 6.2 ADR-0015 修订

ADR-0015 方案 A 的「Agent 不能回复评论」条款由本板块修订为「仅回执型写回」（触发条件：owner 显式 apply 后被动回执；不主动评论、不 LLM 生成）。ADR-0034 承接。

### 6.3 交付物

`comment_client.reply_comment` + `comment_action_service` 成功路径挂钩 + 单测（回执文案 / 失败降级 / 不重复回执）。

## 7. 数据模型与迁移

**无 DB 变更、无新迁移**。复用既有三表：`comments`（幂等 upsert 快照）、`comment_notify_log`（通知去重）、`templates`/`template_versions`（apply 目标）。`idempotency_keys` 不涉（事件不经 IM 管线，同步本身幂等）。

## 8. 权限与后台配置（用户手动，一次性）

开发者后台 → 应用（cli_aa1a8a41f378dcbc）：

1. 「事件与回调 → 事件订阅」添加 `drive.notice.comment_add_v1`（长连接模式已就位）
2. 「权限管理 → 批量导入」贴入 scopes：`docs:document.comment:read`、`docs:document.comment:create`、`docs:document.comment:write_only`、`drive:drive:readonly`、`docx:document:readonly`
3. 创建版本并发布

交付时生成官方一键配置链接（`https://open.feishu.cn/page/launcher?clientID=<APP_ID>&tp=ccm`）供用户点击。

## 9. 测试策略

| 层 | 内容 |
|----|------|
| 单元 | 事件 payload 归一化（notice_type/file_token/operator 提取）；bot 自身过滤；无绑定短路；`reply_comment` SDK 调用封装；回执文案；tick 周期 |
| 集成 | 事件 → sync → notify 全链（mock CommentClient）；apply → 回执调用 + 失败降级；双通道并发幂等（事件与 tick 同 doc 先後触发，notify 仅一次） |
| 回归 | 564 默认层零破坏（重点：原 `FEISHU_API_*` 条件装配相关测试改 SDK 注入后语义等价） |
| 真机 | 用户后台配置后：真文档发评论 → ≤5s IM 收通知；`/comment-apply` → 评论区出现回执 |

预期规模：564 → ~580 passed。

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 官方评论 API 响应结构与既有 mock 假设不符（板块①首真调） | 实施首步先写诊断脚本打真 API 校验结构（复用 diag_doc_blocks.py 模式），再定 SDK 化的字段映射 |
| 后台权限审核周期不可控 | §8 配置在实施前提前交给用户并行操作 |
| ws 回调线程与主 session 竞争 | 独立 event_session/scan_session，复用已验证模式 |
| 事件洪峰（大文档多评论） | sync 全量幂等 + rate_limiter 3/s；事件仅触发不携带正文，处理成本低 |
| bot 回执触发自身事件死循环 | operator 过滤为处理链第一道（§4.2-1） |

## 11. ADR 清单（Phase 11）

| ADR | 决策 |
|-----|------|
| 0032 | CommentClient 迁移 lark-oapi SDK，废除独立静态凭据（修订 Phase 7 httpx 直连设计） |
| 0033 | 评论同步采用事件驱动为主 + 轮询兜底双通道（修订 ADR-0024 纯轮询，依据：官方评论事件仅支持 ws 且项目已具备长连接） |
| 0034 | 回执型评论写回（修订 ADR-0015「不写评论」；仅 owner apply 后被动回执，不做 LLM 生成） |

## 12. 后续动作与阶段门

| 阶段门 | 标准 |
|--------|------|
| 设计门 | 本 spec 用户确认 ✅（2026-08-28） |
| ADR 门 | 0032/0033/0034 用户可评审 |
| 实施门 | plan 用户确认后 inline 实施 |
| 测试门 | 默认层 564+ 零回归；ruff 0 error；真机评论事件 + 回执验证通过 |

**Phase 12 候选**（滚动）：CI（git remote + Actions）/ mypy 增量 / 科研工具（BLAST DB 同步、MSA）/ 执行层 7 项欠账 / zhparser 中文分词。

## 13. 与 Phase 1-10 的接口契约（不变项）

- `CommentSyncService.sync` / `CommentNotifyService.notify_new_pending` / `CommentActionService.apply` 签名不变，板块②③④仅新增调用方
- `_try_market_commands` 12 条指令行为不变（/comments、/comments-sync、/comment-apply 由「未配置」变为真实可用）
- IM 主管线、bind-doc、锚点写入、续期卡片链路零改动

## 14. 实施结果（2026-08-28）

### 14.1 交付物（9 任务全完成）

- **Task 1-4**：CommentClient SDK 化（BaseRequest 模式 + `_to_flat` 防腐层）/ `on_template_upsert -> int` / CommentActionService 回执挂钩 / CommentEventService + bot_info（BaseRequest 调 `GET /bot/v3/info`，真机验证 bot 在顶层无 data 包裹）
- **Task 5**（commit `5760fa9`）：runtime 删 `FEISHU_API_*` 零凭据恒组装；event_session/poll_session 独立隔离；同步清理旧 httpx 集成测试（单测 SDK mock 全覆盖）、e2e E1/E2 改桩客户端、ws_client 条件装配断言改恒组装
- **Task 6**（commit `25b4645`）：dispatcher 注册 `drive.notice.comment_add_v1` + `start_auto_sync_scanner` 守护线程（修复 ws 模式下轮询从未运转缺陷；`interval_sec=0` 允许测试即时触发）
- **Task 7**（commit `e693f5b`）：`scripts/diag_comments.py` 真机诊断
- **Task 8**（commit `6140d63`）：ADR-0032/0033/0034 + 联调指南 §5/§6
- **Task 9**：全量 578 passed + ruff 0 error；真机闭环验证见 14.3

### 14.2 实施期修正（真机驱动）

- **element.type 为 `text_run` 而非 `text`**（commit `5b441b3`）：真机列表返回 `content.elements[].type=="text_run"`，旧解析致全量正文为空、通知不触发；修复后兼容两种 type
- **POST replies 响应 reply 对象直接在 `data` 顶层**（无 `reply` 包裹），`reply_comment` 返回值适配
- bot info 响应无 data 包裹（Task 4 期真机发现，已记入 bot_info 注释）
- **事件字段嵌在 `notice_meta` 内**（commit `444e72b`，2026-08-29 真机验证）：真机事件 file_token/notice_type/from_user_id 在 `notice_meta` 里、comment_id/reply_id 在顶层；旧顶层假设致 file_token 为空 → `ignored_unbound`。`comment_event_to_payload` 重写映射并留原始事件 INFO 日志
- **独立 session 无 commit 数据丢失**（commit `310cc5f`，2026-08-29 真机发现）：repo 只 flush、提交责任在主链路管线，而 event_session/poll_session 无人收尾——事件触发的同步数据滞留未提交事务丢失（现象：handled 但库里查不到）。修复：CommentEventService / CommentAutoSyncWorker 自行 commit/rollback，runtime 注入 session

### 14.3 真机验证（2026-08-28 23:45 轮询通道 / 2026-08-29 00:23 事件通道）

| 验证点 | 结果 |
|---|---|
| 评论列表解析（text_run 适配） | ✅ 5 条评论正文全部正确入库 |
| 轮询兜底 + IM 待处理通知 | ✅ 手动 tick 验证 `notified=1`（等价轮询单轮） |
| /comment-apply | ✅ applied 1 / skipped 4，模板 tpl_test 升至 v1 |
| 回执写回（ADR-0034） | ✅ 文档评论区出现 bot 回复「已按此评论完成修改：模板 tpl_test 已更新至版本 1。」 |
| 评论打标 | ✅ processed_at 置位 |
| 事件接收（后台订阅生效后） | ✅ 00:07:32 首个事件到达，字段映射修复后 500ms 处理完 |
| 事件通道数据落库（commit 修复后） | ✅ 即时入库，bug 期间丢失的 3 条评论自动补齐 |
| 事件通道秒级 IM 通知 | ✅ `/revise` 指令评论 ≤5s 私聊收到待处理通知 |

### 14.4 遗留与后续

1. **群消息权限**：机器人在群内默认只收 @消息；如需群聊使用，申请「获取群组中所有消息」权限（私聊全功能不受影响）
2. 联调过程再现「双 ws_client 进程抢事件」坑（系统 Python 与 venv 同时起）：已按 §6 排障清理，惯例为清旧再单实例启动
3. 评论事件 `notify_new_pending` 只推含指令的评论（防噪音设计）；如需全量评论提醒可后续加开关
