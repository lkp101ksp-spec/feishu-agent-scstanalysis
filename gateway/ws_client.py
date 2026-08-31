"""飞书事件长连接进程（ADR-0031）。

入口：python -m gateway.ws_client
行为：lark-oapi SDK 以 App ID/Secret 建立 WebSocket，接收
- im.message.receive_v1 → 适配为 webhook 兼容 payload → run_im_pipeline
- card.action.trigger    → 适配为平铺 payload → process_card_payload
通道由 SDK 鉴权（auto_reconnect），不走 HTTP 验签；与 uvicorn webhook
共用同一套管线函数，行为零分叉。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackToast,
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from lark_oapi.event.custom import CustomizedEvent

from gateway.app import process_card_payload, run_im_pipeline
from gateway.normalizer import NormalizeError
from gateway.runtime import Runtime, build_runtime
from shared.errors import FeishuAgentError, RateLimitExceededError

logger = logging.getLogger(__name__)


def run_renew_scan_once(renew_scan_service) -> None:
    """同步执行一轮续期卡片扫描（async 方法在独立 event loop 跑一次）。"""
    asyncio.run(renew_scan_service.maybe_send_renew_card())


def start_renew_scanner(rt: Runtime) -> threading.Thread | None:
    """启动续期卡片扫描守护线程：每 interval 秒扫一轮临期绑定并发卡片。"""
    svc = getattr(rt, "renew_scan_service", None)
    if svc is None:
        return None
    interval = getattr(rt, "renew_scan_interval_sec", 60) or 60

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                run_renew_scan_once(svc)
            except Exception:
                logger.exception("renew card scan failed")

    t = threading.Thread(target=loop, daemon=True, name="renew-card-scanner")
    t.start()
    logger.info("renew card scanner started (interval=%ss, threshold=%ss)",
                interval, getattr(svc, "renew_threshold_sec", "?"))
    return t


def comment_event_to_payload(ev: CustomizedEvent) -> dict:
    """评论事件原始 dict → 归一化 payload。

    真机结构（2026-08-29 验证）：file_token/notice_type/from_user_id
    在 notice_meta 内；comment_id/reply_id 在顶层。
    """
    e: dict = dict(ev.event or {})
    meta: dict = e.get("notice_meta") or {}
    operator = meta.get("from_user_id") or {}
    return {
        "notice_type": meta.get("notice_type", ""),
        "file_token": meta.get("file_token", ""),
        "comment_id": e.get("comment_id", ""),
        "operator_open_id": operator.get("open_id", ""),
    }


def run_auto_sync_tick(worker) -> dict:
    """单轮评论轮询兜底（线程内无事件循环，同步直调 tick）。"""
    return worker.tick()


def start_kernel_idle_sweeper(kernel_pool, interval_sec: int = 300):
    """Phase 16：沙箱容器空闲清扫守护线程（每 interval 秒 idle_sweep 一轮）。

    kernel_pool None（引擎未装配）或 interval 0（显式关闭）→ 不启动；
    单轮异常吃掉保线程（下一轮继续）。
    """
    if kernel_pool is None or not interval_sec:
        return None

    def loop() -> None:
        while True:
            time.sleep(interval_sec)
            try:
                removed = kernel_pool.idle_sweep()
                if removed:
                    logger.info("kernel idle sweep removed %d container(s)",
                                removed)
            except Exception:
                logger.exception("kernel idle sweep failed")

    t = threading.Thread(target=loop, daemon=True, name="kernel-idle-sweeper")
    t.start()
    logger.info("kernel idle sweeper started (interval=%ss)", interval_sec)
    return t


def start_auto_sync_scanner(worker, interval_sec: int = 300):
    """启动评论轮询兜底守护线程（ws 模式下 FastAPI startup 钩子不触发）。"""
    if worker is None:
        return None
    # interval_sec=0 允许（测试即时触发）；仅缺省时用 300s
    interval = 300 if interval_sec is None else interval_sec

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                run_auto_sync_tick(worker)
            except Exception:
                logger.exception("auto comment sync tick failed")

    t = threading.Thread(target=loop, daemon=True, name="comment-auto-sync")
    t.start()
    logger.info("auto comment sync scanner started (interval=%ss)", interval)
    return t


def im_event_to_payload(model: P2ImMessageReceiveV1) -> dict:
    """SDK model → webhook 兼容 payload（normalize_im_event 可直接消费）。"""
    msg = model.event.message
    mentions = list(getattr(msg, "mentions", None) or [])
    return {
        "header": {
            "event_id": model.header.event_id,
            "event_type": model.header.event_type,
            "app_id": model.header.app_id,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": model.event.sender.sender_id.open_id},
                "sender_type": model.event.sender.sender_type,
            },
            "message": {
                "chat_id": msg.chat_id,
                "message_id": msg.message_id,
                "message_type": msg.message_type,
                "chat_type": getattr(msg, "chat_type", None) or "",
                "content": msg.content,
                # normalizer 仅做真值判断以剥离 @提及
                "mentions": mentions or None,
            },
        },
    }


def card_event_to_payload(model: P2CardActionTrigger) -> dict:
    """SDK 卡片回调 → 平铺 payload（与卡片 webhook 验签后结构一致）。

    约定：卡片按钮 value 为平铺字段载体（action/session_id/approval_id...），
    适配层将 operator open_id 一并合入。
    """
    payload: dict = dict(model.event.action.value or {})
    operator = model.event.operator
    if operator is not None and getattr(operator, "open_id", None):
        payload.setdefault("open_id", operator.open_id)
    return payload


def _utc_iso_to_beijing_hm(iso: str) -> str:
    """UTC ISO 时间串 → 北京时间 HH:MM（Toast 展示用，解析失败原样返回）。"""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M")


def card_result_to_response(result: dict) -> P2CardActionTriggerResponse | None:
    """管线结果 → 卡片回调 Toast 响应；无需反馈的动作返回 None。

    renew_bind：成功/失败续期 toast（Phase 3）。
    research_writeback（Phase 15 T2）：decided / already_handled / forbidden
    三态 toast，用户点击按钮即有反馈，不必等 research 线程 IM 回执。
    """
    if "new_expires" in result:
        toast = CallBackToast({})
        if result.get("ok"):
            toast.type = "success"
            toast.content = f"已续期至 {_utc_iso_to_beijing_hm(result['new_expires'])}"
        else:
            toast.type = "error"
            toast.content = f"续期失败：{result.get('reason', 'unknown')}"
        resp = P2CardActionTriggerResponse({})
        resp.toast = toast
        return resp
    status = result.get("status", "")
    if status in ("decided", "already_handled", "forbidden"):
        toast = CallBackToast({})
        if status == "decided":
            toast.type = "success"
            toast.content = ("已记录：将写入文档" if result.get("decision") == "approve"
                             else "已记录：跳过写入")
        elif status == "already_handled":
            toast.type = "info"
            toast.content = "该卡片已处理过"
        else:  # forbidden
            toast.type = "error"
            toast.content = "仅任务发起者可操作"
        resp = P2CardActionTriggerResponse({})
        resp.toast = toast
        return resp
    return None


def build_dispatcher(rt: Runtime) -> lark.EventDispatcherHandler:
    """构造事件分发器：IM/卡片事件闭包到公共管线（异常吃掉保连接）。"""

    def on_im(data: P2ImMessageReceiveV1) -> None:
        app_id = data.header.app_id or "default"
        try:
            result = run_im_pipeline(rt.app, app_id, im_event_to_payload(data))
            logger.info("ws im handled: %s", result)
        except RateLimitExceededError as e:
            logger.warning("ws im rate_limited: %s", e)
        except NormalizeError as e:
            logger.info("ws im skipped: %s", e)
        except FeishuAgentError:
            logger.exception("ws im pipeline failed")
        except Exception:
            logger.exception("ws im unexpected error")

    def on_card(data: P2CardActionTrigger) -> P2CardActionTriggerResponse | None:
        try:
            result = process_card_payload(rt.app, card_event_to_payload(data))
            logger.info("ws card handled: %s", result)
            return card_result_to_response(result)
        except Exception:
            logger.exception("ws card unexpected error")
            return None

    def on_doc_comment(ev: CustomizedEvent) -> None:
        """文档评论事件：防死循环过滤 + 绑定校验 + sync/notify（ADR-0033）。"""
        svc = getattr(rt, "comment_event_service", None)
        if svc is None:
            return
        logger.info("ws raw comment event: %s", getattr(ev, "event", None))
        payload = comment_event_to_payload(ev)
        result = svc.handle(file_token=payload["file_token"],
                            operator_open_id=payload["operator_open_id"])
        logger.info("ws comment event handled: %s", result)

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_im)
        .register_p2_card_action_trigger(on_card)
        .register_p2_customized_event("drive.notice.comment_add_v1",
                                      on_doc_comment)
        .build()
    )


def main() -> None:
    """长连接进程入口：组装 runtime → 建 ws client → 阻塞接收。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    rt = build_runtime()
    start_renew_scanner(rt)
    start_auto_sync_scanner(
        rt.auto_sync_worker,
        interval_sec=rt.settings.comment_sync_interval_sec,
    )
    # Phase 16：沙箱容器空闲清扫（引擎未装配时 orchestrator.kernel_pool 不存在）
    start_kernel_idle_sweeper(
        getattr(rt.orchestrator, "kernel_pool", None),
        interval_sec=getattr(rt.settings, "kernel_sweep_interval_sec", 300),
    )
    client = lark.ws.Client(
        rt.settings.feishu.app_id,
        rt.settings.feishu.app_secret,
        log_level=lark.LogLevel.INFO,
        event_handler=build_dispatcher(rt),
        auto_reconnect=True,
    )
    logger.info("ws long-connection starting (app_id=%s)", rt.settings.feishu.app_id)
    client.start()


if __name__ == "__main__":
    main()
