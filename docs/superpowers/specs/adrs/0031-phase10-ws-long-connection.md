# ADR-0031: 飞书事件长连接接入（WebSocket，替代内网穿透）

日期：2026-08-27
状态：已接受（Phase 10 联调补充轮）

## 背景

Phase 10 就绪包的内网穿透方案（联调指南 §3 方案 B）依赖第三方隧道，稳定性与合规存在顾虑，且本地开发需要公网暴露。飞书开放平台官方提供「长连接」订阅方式：`lark-oapi` SDK 以 App ID/Secret 主动外连飞书服务器，事件经 WebSocket 推送到本地进程，无需公网地址。

## 决策

1. 引入官方 SDK 依赖 `lark-oapi`（锁版本入 requirements-lock.txt）。
2. 新增 `gateway/ws_client.py` 作为**独立长连接进程**入口（`python -m gateway.ws_client`）：
   - IM 消息事件（`im.message.receive_v1`）与卡片回调（`card.action.trigger`）均走长连接；
   - SDK model → webhook 兼容 payload 的适配层，复用 gateway 既有管线（限流/归一化/幂等/orchestrator），**零逻辑分叉**；
   - 长连接通道由 SDK 鉴权（auto_reconnect=True），不再做 HTTP 验签。
3. `gateway/app.py` 把 webhook 路由的「限流→归一化→幂等→process→link_task」抽为模块级公共管线函数，webhook 路由（验签后）与 ws 进程共用。
4. 新增 `gateway/runtime.py` 生产组装：真实 LarkCLI adapters + LLMRouter（settings）+ 全量 repos/services + `create_app`，补齐 Phase 1 以来缺失的生产组装入口。
5. webhook 路由保留（部署上云仍可用 HTTP 回调），两种入口并存。

## 后果

- 正面：本地联调零公网依赖；uvicorn 仅服务模板管理 API/健康检查；事件处理与 HTTP 路径行为一致（同一管线函数）。
- 负面/权衡：多一个常驻进程；SDK model→dict 适配层需跟随 SDK 版本（已锁版本）；ws 进程内服务共享单 Session（低并发联调可接受，多实例部署需另行改造——Phase 9 ADR-0024 的分布式锁议题顺延）。
- 租户侧变化：事件订阅方式选「使用长连接接收事件」；`FEISHU_WEBHOOK_SECRET` 仅 webhook 模式需要。

## 关联

- ADR-0028（venv 锁定，lark-oapi 入锁文件）
- ADR-0030（就绪包，联调指南 §3/§4 随本决策更新）
