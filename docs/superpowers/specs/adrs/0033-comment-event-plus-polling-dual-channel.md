# ADR-0033: 评论感知双通道（事件为主 + 轮询兜底）

日期：2026-08-28
状态：已接受（Phase 11，修订 ADR-0024 纯轮询）

## 背景

评论感知此前仅有 auto_sync_worker 定时轮询（300s 全量拉取），存在两个问题：① 感知延迟最高一个轮询周期；② ws_client 长连接模式下无 ASGI server，FastAPI startup 钩子不触发，轮询 worker 实际**从未运转**（Phase 10 联调遗漏缺陷）。官方评论事件 `drive.notice.comment_add_v1` 仅支持 WebSocket 长连接推送，而项目已具备 ws 长连接进程（ADR-0031）。

## 决策

1. **事件为主**：ws_client dispatcher 注册 `drive.notice.comment_add_v1`（`register_p2_customized_event`，CustomizedEvent.event 为原始 dict），回调走新增 `CommentEventService`。
2. **轮询兜底**：`start_auto_sync_scanner` 守护线程（daemon，对齐 renew scanner 模式）修复 ws 模式下轮询缺口；uvicorn 模式仍走 startup 钩子，两入口幂等并存。
3. **线程隔离**：事件回调线程用独立 `event_session`、轮询线程用独立 `poll_session`，严禁与主管线 Session 跨线程共享（SQLAlchemy Session 非线程安全）。
4. **防死循环**：过滤 `operator_id == bot open_id`（bot 回执自身触发的评论事件不再回流）；bot open_id 经 `GET /open-apis/bot/v3/info` 拉取并进程级缓存，**拿不到时保守跳过全部评论事件**（宁漏勿循环）。
5. 事件处理链：防循环 → 找 `bound_doc_id == file_token` 且未过期的活跃 session（未绑定忽略）→ sync（幂等 upsert）→ notify（去重表）。

## 备选方案

- **纯事件**：弃——事件丢失（ws 断连窗口）无补偿；轮询兜底保证最终感知。
- **纯轮询**（ADR-0024 原状）：弃——300s 延迟体验差，且 ws 模式轮询未运转的缺陷暴露单通道风险。

## 负面后果

- 双通道并发依赖 sync 幂等（comment_id upsert）与 notify 去重表（comment_notify_log），二者已具备；理论同时触发时 IM 通知至多一条。
- bot info 拉取失败被进程级缓存为 None，重启前评论事件通道静默失效（轮询兜底仍在）。

## 回滚条件

移除 dispatcher 的评论事件注册即回退纯轮询（单行删除）；轮询线程独立可单独停用。

## 关联

- ADR-0031（ws 长连接基础设施）
- ADR-0032（SDK 化使双通道零凭据）
- ADR-0024（被修订的纯轮询决策）
