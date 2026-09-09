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
import atexit
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackCard,
    CallBackToast,
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from lark_oapi.event.custom import CustomizedEvent

from gateway.app import process_card_payload, run_im_pipeline
from gateway.normalizer import NormalizeError
from gateway.runtime import Runtime, build_runtime
from orchestrator.bio_workspace_gc import _SweepResult, sweep
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


def _bio_gc_sweep_once(settings) -> _SweepResult:
    """单轮 bio_workspace GC（线程内同步直调；异常由线程 loop 吃掉）。"""
    return sweep(
        settings.bio_workspace_root,
        ttl_sec=settings.bio_workspace_ttl_sec,
        cap_bytes=settings.bio_workspace_cap_gb * (1024 ** 3),
        grace_sec=settings.bio_workspace_grace_sec,
    )


def start_bio_workspace_gc_sweeper(settings):
    """Phase 23：bio_workspace 磁盘治理守护线程（每 interval 秒 sweep 一轮）。

    enabled=False 或 interval=0 → 不启动返回 None；单轮异常吃掉保线程。
    """
    if not getattr(settings, "bio_workspace_gc_enabled", True):
        return None
    interval = getattr(settings, "bio_workspace_gc_interval_sec", 3600)
    if not interval:
        return None

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                result = _bio_gc_sweep_once(settings)
                if result["ttl_deleted"] or result["lru_deleted"]:
                    logger.info(
                        "bio workspace gc: ttl=%s lru=%s freed=%d bytes",
                        result["ttl_deleted"], result["lru_deleted"],
                        result["freed_bytes"])
            except Exception:
                logger.exception("bio workspace gc sweep failed")

    t = threading.Thread(target=loop, daemon=True, name="bio-workspace-gc")
    t.start()
    logger.info(
        "bio workspace gc sweeper started (interval=%ss, ttl=%ss, cap=%sGB)",
        interval, getattr(settings, "bio_workspace_ttl_sec", "?"),
        getattr(settings, "bio_workspace_cap_gb", "?"))
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


def start_db_health_monitor(rt: Runtime, interval_sec: int = 30):
    """DB 健康监控守护线程（2026-09-09 事故驱动：pg 掉线静默离线）。

    每轮 SELECT 1 探活；健康↔故障跃迁时经 IMAdapter 给
    FEISHU_ADMIN_OPEN_IDS 发飞书告警（故障期指数退避）。
    无管理员配置时仍记日志跃迁，只是不发 IM。
    """
    from gateway.health_monitor import DBHealthMonitor, probe_pg_url

    im = getattr(rt.orchestrator, "im_adapter", None)
    admin_ids = [
        x.strip()
        for x in os.environ.get("FEISHU_ADMIN_OPEN_IDS", "").split(",")
        if x.strip()
    ]

    def alert(text: str) -> None:
        if im is None:
            return
        for oid in admin_ids:
            im.send(oid, "open_id", "text", text)

    # probe 走 psycopg 短超时直连而非共享 engine：docker stop 后
    # engine.connect() 会无限挂起（2026-09-09 真机复现），监控线程被拖死
    mon = DBHealthMonitor(probe=lambda: probe_pg_url(rt.settings.database_url),
                          alert=alert, interval_sec=interval_sec)

    def loop() -> None:
        while True:
            mon.tick()
            time.sleep(mon.next_delay)

    t = threading.Thread(target=loop, daemon=True, name="db-health-monitor")
    t.start()
    logger.info("db health monitor started (interval=%ss, admins=%d)",
                interval_sec, len(admin_ids))
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
    # Phase 30：模型切换 toast（成功即时生效；拒绝按 reason 提示）
    # 双卡片流程（ut-7）：model_pick/model_back 原地换卡面；切换成功
    # 附带最新状态卡把选模型卡刷回卡 1。
    if status in ("model_pick", "model_back"):
        resp = P2CardActionTriggerResponse({})
        resp.card = CallBackCard({"type": "raw", "data": result["card"]})
        return resp
    if status == "model_switched":
        slot = "主" if result.get("slot") == "primary" else "备"
        toast = CallBackToast({})
        toast.type = "success"
        toast.content = f"已切换 {result.get('to', '')} 为{slot}模型（即时生效，无需重启）"
        resp = P2CardActionTriggerResponse({})
        resp.toast = toast
        if result.get("card"):
            resp.card = CallBackCard({"type": "raw", "data": result["card"]})
        return resp
    if status in ("model_switch_denied", "model_switch_unavailable"):
        reason = result.get("reason", "")
        fallback_text = ("模型切换不可用" if status == "model_switch_unavailable"
                         else f"切换失败：{reason}")
        text = {"forbidden": "仅管理员可切换模型",
                "unknown_provider": "无效候选（候选池无此名字）",
                "bad_slot": "无效槽位"}.get(reason, fallback_text)
        toast = CallBackToast({})
        toast.type = "error"
        toast.content = text
        resp = P2CardActionTriggerResponse({})
        resp.toast = toast
        return resp
    # Phase 38：意图预判确认卡反馈（批准/忽略均原地换面去按钮）
    if status in ("intent_approved", "intent_denied"):
        route_label = {"research": "/research", "code": "/code"}.get(
            result.get("route", ""), "")
        toast = CallBackToast({})
        toast.type = "success" if status == "intent_approved" else "info"
        toast.content = (
            f"已受理，按 {route_label} 开始执行…"
            if status == "intent_approved" else "已忽略，按普通聊天处理")
        resp = P2CardActionTriggerResponse({})
        resp.toast = toast
        if result.get("card"):
            resp.card = CallBackCard({"type": "raw", "data": result["card"]})
        return resp
    if status in ("intent_expired", "intent_unavailable"):
        toast = CallBackToast({})
        toast.type = "info" if status == "intent_expired" else "error"
        toast.content = ("该卡片已失效，请重新发送指令"
                         if status == "intent_expired" else "意图预判服务未配置")
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
                            operator_open_id=payload["operator_open_id"],
                            comment_id=payload["comment_id"])
        logger.info("ws comment event handled: %s", result)

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_im)
        .register_p2_card_action_trigger(on_card)
        .register_p2_customized_event("drive.notice.comment_add_v1",
                                      on_doc_comment)
        .build()
    )


_PIDFILE = Path(__file__).resolve().parent.parent / ".ws_client.pid"
# kernel32 与 get_last_error 均 Windows 专属符号（Linux typeshed 无此属性）。
# mypy 按运行平台对 sys.platform 守卫做可达性分析——三元表达式不触发该
# 特判，必须用 if/else 块：Linux CI 跳过 win32 分支，本机 Windows 跳过
# else 分支，双平台过门禁。_pid_alive/_terminate 只在 Windows 生产路径
# 调用，测试一律 monkeypatch。
if sys.platform == "win32":
    from ctypes import WinDLL, get_last_error

    _KERNEL32: WinDLL | None = WinDLL("kernel32", use_last_error=True)
else:
    _KERNEL32 = None
    get_last_error = None  # 类型占位：调用点在 assert 之后，非 Windows 不可达
_ERROR_INVALID_PARAMETER = 87


def _pid_alive(pid: int) -> bool:
    """Windows 探活：OpenProcess 可打开即活着。

    仅探 pid 存活不校验 cmdline——pid 复用误判概率低（守卫目标是防
    人为双启，不是安全边界）；GetLastError==87（INVALID_PARAMETER）
    意味 pid 不存在判死，其余失败（权限等）一律按活着保守处理。
    """
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    assert _KERNEL32 is not None  # 仅 Windows 生产路径调用（见模块头注释）
    h = _KERNEL32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return get_last_error() != _ERROR_INVALID_PARAMETER
    _KERNEL32.CloseHandle(h)
    return True


def _terminate(pid: int) -> None:
    """--force 杀旧进程并等待退出（0.5s 轮询，上限 5s）。"""
    assert _KERNEL32 is not None  # 仅 Windows 生产路径调用（见模块头注释）
    h = _KERNEL32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h:
        _KERNEL32.TerminateProcess(h, 1)
        _KERNEL32.CloseHandle(h)
    for _ in range(10):
        if not _pid_alive(pid):
            return
        time.sleep(0.5)


def _release_pidfile() -> None:
    """退出清理：仅当 pidfile 内容仍是本进程 pid（不误删接管者）。"""
    try:
        if _PIDFILE.read_text().strip() == str(os.getpid()):
            _PIDFILE.unlink(missing_ok=True)
    except OSError:
        pass


def acquire_single_instance(force: bool = False) -> None:
    """单实例守卫（Phase 22）：活实例拒绝（--force 杀旧接管），stale 接管。"""
    if _PIDFILE.exists():
        try:
            old_pid = int(_PIDFILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
            if not force:
                logger.error(
                    "ws_client already running (pid=%s); "
                    "use --force to replace", old_pid)
                raise SystemExit(1)
            logger.warning("--force: terminating old ws_client (pid=%s)",
                           old_pid)
            _terminate(old_pid)
        elif old_pid != os.getpid():
            logger.warning("stale pidfile (pid=%s) -> take over", old_pid)
    _PIDFILE.write_text(str(os.getpid()))
    atexit.register(_release_pidfile)


def main() -> None:
    """长连接进程入口：组装 runtime → 建 ws client → 阻塞接收。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # 文件日志（2026-09-09 事故驱动：隐藏窗口启动时 stderr 全丢，
    # 进程死亡原因无从排查；logs/ 已 gitignore，滚动 5MB×3）
    from logging.handlers import RotatingFileHandler

    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "ws_client.log", maxBytes=5 * 1024 * 1024,
        backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logging.getLogger().addHandler(file_handler)
    # Phase 22：单实例守卫（四轮真机双实例复发；--force 显式替换）
    acquire_single_instance(force="--force" in sys.argv)
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
    # Phase 23：bio_workspace 磁盘治理（TTL+LRU 周期清理）
    start_bio_workspace_gc_sweeper(rt.settings)
    # DB 健康监控：pg 掉线/恢复告警（2026-09-09 静默离线事故）
    start_db_health_monitor(rt)
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
