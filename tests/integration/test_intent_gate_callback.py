"""Phase 38 research_intent 卡片回调集成测试：网关分支 + 转 /research 全链。"""
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gateway.app import create_app
from orchestrator.intent_gate import IntentGateService
from persistence.models import Base
from persistence.repositories.audit_repo import AuditRepo


@pytest.fixture
def client_with_gate():
    """真 IntentGateService + MagicMock research/coding runner 的卡片环境。"""
    llm = MagicMock()
    llm.chat.return_value = '{"route": "research"}'
    im = MagicMock()
    gate = IntentGateService(llm=llm, im=im)
    runner = MagicMock()
    runner.handle.return_value = {"status": "research_accepted"}
    coding_runner = MagicMock()
    coding_runner.handle.return_value = {"status": "code_accepted"}
    orch = SimpleNamespace(intent_gate=gate, research_runner=runner,
                           coding_runner=coding_runner)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    app = create_app(secret="phase2-secret", orchestrator=orch)
    app.state.session_factory = factory
    return TestClient(app), gate, runner, coding_runner, factory


def _post_card(client, payload: dict):
    body = json.dumps(payload).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook/lark/card", content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )


def _offer(gate, text="对 f1e89bf88edc 做双联体检测"):
    incoming = SimpleNamespace(
        text=text, chat_id="oc_1", sender_open_id="ou_1",
        message_id="om_1", chat_type="p2p")
    return gate.maybe_offer(incoming)["intent_id"]


def _payload(iid, *, decision="approve", owner="ou_1", open_id="ou_1",
             route="research"):
    value = {"action": "research_intent", "intent_id": iid,
             "decision": decision, "owner": owner, "open_id": open_id}
    if decision == "approve":
        value["route"] = route
    return value


def test_approve_routes_to_research_runner(client_with_gate):
    """确认执行：research_runner.handle 收到 /research 化消息，返回换面卡。"""
    client, gate, runner, coding_runner, factory = client_with_gate
    iid = _offer(gate)
    resp = _post_card(client, _payload(iid))
    body = resp.json()
    assert body["ok"] and body["status"] == "intent_approved"
    assert body["route"] == "research"
    assert body["card"]["header"]["title"]["content"] == "研究任务已受理"
    runner.handle.assert_called_once()
    coding_runner.handle.assert_not_called()
    incoming = runner.handle.call_args.args[0]
    assert incoming.text == "/research 对 f1e89bf88edc 做双联体检测"
    assert incoming.sender_open_id == "ou_1" and incoming.chat_id == "oc_1"


def test_approve_code_route_goes_to_coding_runner(client_with_gate):
    """纠偏改道：route=code 时转 coding_runner，消息前补 /code 前缀。"""
    client, gate, runner, coding_runner, factory = client_with_gate
    iid = _offer(gate)
    resp = _post_card(client, _payload(iid, route="code"))
    body = resp.json()
    assert body["ok"] and body["route"] == "code"
    assert body["card"]["header"]["title"]["content"] == "代码任务已受理"
    coding_runner.handle.assert_called_once()
    runner.handle.assert_not_called()
    incoming = coding_runner.handle.call_args.args[0]
    assert incoming.text == "/code 对 f1e89bf88edc 做双联体检测"


def test_deny_does_not_start_research(client_with_gate):
    client, gate, runner, coding_runner, factory = client_with_gate
    iid = _offer(gate)
    resp = _post_card(client, _payload(iid, decision="deny"))
    body = resp.json()
    assert body["ok"] and body["status"] == "intent_denied"
    runner.handle.assert_not_called()
    coding_runner.handle.assert_not_called()


def test_owner_mismatch_forbidden(client_with_gate):
    client, gate, runner, coding_runner, factory = client_with_gate
    iid = _offer(gate)
    resp = _post_card(client, _payload(iid, open_id="ou_other"))
    assert resp.json()["status"] == "forbidden"
    runner.handle.assert_not_called()
    coding_runner.handle.assert_not_called()


def test_gate_not_configured_unavailable():
    """orch 无意图闸：返回 intent_unavailable（toast 提示服务未配置）。"""
    app = create_app(secret="phase2-secret", orchestrator=SimpleNamespace())
    client = TestClient(app)
    resp = _post_card(client, _payload("x"))
    assert resp.json() == {"ok": False, "status": "intent_unavailable"}


def test_approve_audited_with_intent_target(client_with_gate):
    """批准落专项审计：intent_approved，target_id=intent_id，含路由。"""
    client, gate, runner, coding_runner, factory = client_with_gate
    iid = _offer(gate)
    _post_card(client, _payload(iid))
    s = factory()
    try:
        rows = [r for r in AuditRepo(s).list_recent(limit=20)
                if r.action == "intent_approved"]
    finally:
        s.close()
    assert len(rows) == 1
    assert rows[0].target_id == iid
    assert rows[0].detail_json["route"] == "research"
    assert "双联体检测" in rows[0].detail_json["text"]
