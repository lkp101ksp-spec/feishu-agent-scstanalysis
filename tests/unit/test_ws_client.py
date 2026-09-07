"""Phase 10 联调补充：ws 长连接客户端单测（ADR-0031）。

不建立真实 WebSocket 连接；用 SDK model 的 dict 构造能力验证适配层
与生产组装（runtime）。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

from gateway.normalizer import normalize_im_event
from gateway.runtime import Runtime, build_runtime
from gateway.ws_client import (
    build_dispatcher,
    card_event_to_payload,
    card_result_to_response,
    comment_event_to_payload,
    im_event_to_payload,
    run_auto_sync_tick,
    run_renew_scan_once,
    start_auto_sync_scanner,
    start_kernel_idle_sweeper,
    start_renew_scanner,
)

# --- IM 事件 fixture（真实 v2 schema 结构） ---


def _im_model(text: str = "分析这个序列", mentions=None) -> P2ImMessageReceiveV1:
    d = {
        "header": {
            "event_id": "e_1", "event_type": "im.message.receive_v1",
            "app_id": "cli_x", "tenant_key": "t",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_1"}, "sender_type": "user",
            },
            "message": {
                "chat_id": "oc_1", "message_id": "om_1", "message_type": "text",
                "chat_type": "p2p",
                "content": json.dumps({"text": text}),
            },
        },
    }
    if mentions is not None:
        d["event"]["message"]["mentions"] = mentions
    return P2ImMessageReceiveV1(d)


def _card_model(value: dict, with_operator: bool = True) -> P2CardActionTrigger:
    d = {
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "token": "tb_1",
            "action": {"tag": "button", "value": value},
        },
    }
    if with_operator:
        d["event"]["operator"] = {"open_id": "ou_9"}
    return P2CardActionTrigger(d)


def test_im_event_to_payload_feeds_normalizer():
    """适配产物能被 normalize_im_event 直接消费（webhook 管线同构）。"""
    payload = im_event_to_payload(_im_model("帮我分析"))
    incoming = normalize_im_event(payload)
    assert incoming.chat_id == "oc_1"
    assert incoming.message_id == "om_1"
    assert incoming.sender_open_id == "ou_1"
    assert incoming.text == "帮我分析"
    assert payload["header"]["app_id"] == "cli_x"


def test_im_event_with_mentions_strips_at():
    """带 @提及时：mentions 传递 → normalizer 剥离 @ 文本。"""
    payload = im_event_to_payload(_im_model(
        "@agent 帮我分析", mentions=[{"key": "@_user_1", "id": {"open_id": "ou_b"}}],
    ))
    assert payload["event"]["message"]["mentions"] is not None
    incoming = normalize_im_event(payload)
    assert incoming.text == "帮我分析"


def test_card_event_to_payload_flattens_value():
    """卡片 value 平铺 + operator open_id 合入（open_id 不覆盖已有）。"""
    p1 = card_event_to_payload(_card_model({"action": "renew_bind", "session_id": "s1"}))
    assert p1["action"] == "renew_bind"
    assert p1["session_id"] == "s1"
    assert p1["open_id"] == "ou_9"

    p2 = card_event_to_payload(_card_model(
        {"action": "x", "open_id": "ou_self"}, with_operator=True,
    ))
    assert p2["open_id"] == "ou_self"  # value 优先


def test_card_result_to_response_success_toast():
    """renew 成功 → success Toast，UTC 时间转北京时间 HH:MM。"""
    resp = card_result_to_response(
        {"ok": True, "new_expires": "2026-08-28T02:57:18+00:00"})
    assert resp is not None
    assert resp.toast.type == "success"
    assert resp.toast.content == "已续期至 10:57"


def test_card_result_to_response_error_toast():
    """renew 失败 → error Toast，携带失败原因。"""
    resp = card_result_to_response({"ok": False, "new_expires": "", "reason": "no bind"})
    assert resp is not None
    assert resp.toast.type == "error"
    assert "no bind" in resp.toast.content


def test_card_result_to_response_none_for_silent_action():
    """非续期动作（无 new_expires/status）→ 不弹 Toast。"""
    assert card_result_to_response({"ok": True}) is None


# --- Phase 15 T2：research_writeback 决策 Toast ---

def test_card_result_to_response_writeback_decided_approve():
    """发起者点同意 → success Toast「已记录：将写入文档」。"""
    resp = card_result_to_response(
        {"ok": True, "status": "decided", "decision": "approve"})
    assert resp is not None
    assert resp.toast.type == "success"
    assert resp.toast.content == "已记录：将写入文档"


def test_card_result_to_response_writeback_decided_deny():
    """发起者点跳过 → success Toast「已记录：跳过写入」。"""
    resp = card_result_to_response(
        {"ok": True, "status": "decided", "decision": "deny"})
    assert resp is not None
    assert resp.toast.type == "success"
    assert resp.toast.content == "已记录：跳过写入"


def test_card_result_to_response_writeback_already_handled():
    """重复点击 → info Toast「该卡片已处理过」。"""
    resp = card_result_to_response(
        {"ok": False, "status": "already_handled", "decision": ""})
    assert resp is not None
    assert resp.toast.type == "info"
    assert resp.toast.content == "该卡片已处理过"


def test_card_result_to_response_writeback_forbidden():
    """非发起者点击 → error Toast「仅任务发起者可操作」。"""
    resp = card_result_to_response({"ok": False, "status": "forbidden"})
    assert resp is not None
    assert resp.toast.type == "error"
    assert resp.toast.content == "仅任务发起者可操作"


# --- Phase 30 ut-7：模型切换双卡片（回调响应卡原地换卡面） ---

def test_card_result_to_response_model_pick_swaps_card():
    """model_pick → 无 toast，响应卡 = 选模型卡（type=raw 原地替换）。"""
    picker = {"header": {"title": {"tag": "plain_text", "content": "选择主模型"}},
              "elements": []}
    resp = card_result_to_response(
        {"ok": True, "status": "model_pick", "card": picker})
    assert resp is not None
    assert resp.toast is None
    assert resp.card.type == "raw" and resp.card.data == picker


def test_card_result_to_response_model_switched_toast_and_card():
    """切换成功 → success toast + 附带最新状态卡刷回卡 1。"""
    status_card = {"header": {"title": {"tag": "plain_text", "content": "/model"}},
                   "elements": []}
    resp = card_result_to_response(
        {"ok": True, "status": "model_switched", "slot": "primary",
         "to": "kimi", "card": status_card})
    assert resp is not None
    assert resp.toast.type == "success" and "kimi" in resp.toast.content
    assert resp.card is not None and resp.card.data == status_card


def test_build_dispatcher_smoke():
    """dispatcher 可构造（注册 IM + 卡片回调，不建立连接）。"""
    rt = Runtime(app=FastAPI(), orchestrator=object(), settings=object())
    dispatcher = build_dispatcher(rt)
    assert dispatcher is not None


def test_ws_client_module_has_main():
    """进程入口存在（python -m gateway.ws_client）。"""
    from gateway import ws_client
    assert callable(ws_client.main)


# --- 续期卡片扫描线程 ---

def test_run_renew_scan_once_invokes_scan():
    """一轮扫描：async maybe_send_renew_card 被同步包装调用一次。"""
    from unittest.mock import AsyncMock, MagicMock

    svc = MagicMock()
    svc.maybe_send_renew_card = AsyncMock(return_value=None)
    run_renew_scan_once(svc)
    svc.maybe_send_renew_card.assert_awaited_once()


def test_start_renew_scanner_thread_scans():
    """扫描线程按 interval 周期调用扫描（等首轮即可）。"""
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    svc = MagicMock()
    svc.maybe_send_renew_card = AsyncMock(return_value=None)
    svc.renew_threshold_sec = 300
    rt = SimpleNamespace(renew_scan_service=svc,
                         renew_scan_interval_sec=0.05)
    t = start_renew_scanner(rt)
    assert t is not None and t.daemon
    for _ in range(100):
        if svc.maybe_send_renew_card.await_count >= 1:
            break
        time.sleep(0.02)
    assert svc.maybe_send_renew_card.await_count >= 1


def test_start_renew_scanner_no_service_is_noop():
    """renew_scan_service 为 None：不启动线程，返回 None。"""
    from types import SimpleNamespace

    rt = SimpleNamespace(renew_scan_service=None, renew_scan_interval_sec=60)
    assert start_renew_scanner(rt) is None


# --- Phase 16：沙箱容器空闲清扫线程 ---


def test_start_kernel_idle_sweeper_sweeps():
    """清扫线程按 interval 周期调用 idle_sweep（等首轮即可）。"""
    import time
    from unittest.mock import MagicMock

    pool = MagicMock()
    pool.idle_sweep.return_value = 0
    t = start_kernel_idle_sweeper(pool, interval_sec=0.05)
    assert t is not None and t.daemon
    for _ in range(100):
        if pool.idle_sweep.call_count >= 1:
            break
        time.sleep(0.02)
    assert pool.idle_sweep.call_count >= 1


def test_start_kernel_idle_sweeper_noop_cases():
    """kernel_pool None（引擎未装配）或 interval 0（显式关闭）→ 不启动。"""
    from unittest.mock import MagicMock

    assert start_kernel_idle_sweeper(None, interval_sec=300) is None
    assert start_kernel_idle_sweeper(MagicMock(), interval_sec=0) is None


def test_start_kernel_idle_sweeper_swallows_exception():
    """单轮 idle_sweep 异常被吃掉保线程（下一轮继续跑）。"""
    import time
    from unittest.mock import MagicMock

    pool = MagicMock()
    pool.idle_sweep.side_effect = RuntimeError("docker down")
    t = start_kernel_idle_sweeper(pool, interval_sec=0.01)
    assert t is not None
    for _ in range(100):
        if pool.idle_sweep.call_count >= 2:
            break
        time.sleep(0.02)
    # 异常后线程仍存活且继续调用
    assert pool.idle_sweep.call_count >= 2
    assert t.is_alive()


# --- 生产组装（runtime） ---


@pytest.fixture()
def _runtime_env(monkeypatch):
    """占位环境 + 内存库：验证真实对象图可装配。"""
    for key in (
        "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WEBHOOK_SECRET",
        "LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
        "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL",
    ):
        monkeypatch.setenv(key, f"ws-test-{key.lower()}")
    monkeypatch.delenv("FEISHU_BASE_APP_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_DRIVE_PARENT_TOKEN", raising=False)

    from sqlalchemy import create_engine

    from persistence.models import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    import persistence.engine as pe
    monkeypatch.setattr(pe, "_engine", engine)
    monkeypatch.setattr(
        pe, "_SessionLocal",
        __import__("sqlalchemy").orm.sessionmaker(
            bind=engine, expire_on_commit=False, autoflush=False),
    )
    yield engine


def test_build_runtime_assembles_real_graph(_runtime_env):
    """全量真实组件装配：app/orchestrator 类型正确，评论子系统 SDK 零凭据恒组装。"""
    from config.settings import load_settings
    from orchestrator.app import Orchestrator

    settings = load_settings()
    rt = build_runtime(settings=settings)
    assert isinstance(rt, Runtime)
    assert isinstance(rt.app, FastAPI)
    assert isinstance(rt.orchestrator, Orchestrator)
    # 评论子系统 SDK 化后零凭据恒组装（ADR-0032）
    assert rt.app.state.ctx.comment_service is not None
    assert rt.app.state.ctx.unified_search_service is not None
    assert rt.app.state.ctx.bind_doc_service is not None
    # Phase 30：模型热切换双通道挂载（orch 出卡 + ctx 回调，ut-7 修复）
    assert rt.orchestrator.model_switch_service is not None
    assert rt.app.state.ctx.model_switch_service is not None
    # health 路由可用
    from fastapi.testclient import TestClient
    assert TestClient(rt.app).get("/health").json() == {"status": "ok"}


def test_build_runtime_comment_dual_channel_handles(_runtime_env):
    """评论全家桶 + 事件/轮询双通道句柄恒组装（独立 session，ADR-0033）。"""
    from config.settings import load_settings

    rt = build_runtime(settings=load_settings())
    assert rt.app.state.ctx.comment_service is not None
    assert rt.app.state.ctx.comment_sync_service is not None
    assert rt.app.state.ctx.auto_sync_worker is not None
    assert rt.comment_event_service is not None
    assert rt.auto_sync_worker is not None


# --- Phase 11：评论事件接线 + 轮询兜底守护线程（ADR-0033） ---


def test_comment_event_to_payload_extracts_fields():
    """CustomizedEvent(dict) → 归一化 payload（真机结构：字段在 notice_meta 内）。"""
    from unittest.mock import MagicMock

    ev = MagicMock()
    ev.event = {
        "comment_id": "c1", "reply_id": "c1_r1", "is_mentioned": False,
        "notice_meta": {
            "file_token": "doccnX", "file_type": "docx",
            "notice_type": "add_reply",
            "from_user_id": {"open_id": "ou_teacher",
                             "union_id": "on_x", "user_id": None},
            "from_user_type": "user",
            "to_user_id": {"open_id": "ou_bot"},
        },
        "visibility": "public",
    }
    p = comment_event_to_payload(ev)
    assert p == {"notice_type": "add_reply", "file_token": "doccnX",
                 "comment_id": "c1",
                 "operator_open_id": "ou_teacher"}


def test_run_auto_sync_tick_once():
    """tick_once 调 worker.tick 一次并返回结果。"""
    from unittest.mock import MagicMock

    worker = MagicMock()
    worker.tick.return_value = {"synced": 1}
    assert run_auto_sync_tick(worker) == {"synced": 1}
    worker.tick.assert_called_once()


def test_start_auto_sync_scanner_daemon_thread():
    """扫描线程 daemon + 周期调用 tick；worker None 时 noop 返回 None。"""
    import time as _t
    from unittest.mock import MagicMock

    worker = MagicMock()
    calls = []
    worker.tick.side_effect = lambda: (calls.append(1),
                                       _t.sleep(0.05))[0]
    t = start_auto_sync_scanner(worker, interval_sec=0)
    assert t is not None and t.daemon is True
    _t.sleep(0.2)
    worker.tick.assert_called()
    assert start_auto_sync_scanner(None) is None
