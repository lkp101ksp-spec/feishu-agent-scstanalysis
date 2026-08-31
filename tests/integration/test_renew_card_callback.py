import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


@pytest.fixture
def client_with_bind_doc_service():
    bind_doc_service = MagicMock()
    from datetime import datetime, timedelta, timezone
    bind_doc_service.renew.return_value = datetime.now(timezone.utc) + timedelta(seconds=1800)
    client = TestClient(create_app(
        secret="phase2-secret",
        orchestrator=object(),
        bind_doc_service=bind_doc_service,
    ))
    return client, bind_doc_service


def test_card_renew_callback_routes_to_bind_doc_service(client_with_bind_doc_service):
    client, bind_doc_service = client_with_bind_doc_service
    body = json.dumps({
        "action": "renew_bind",
        "session_id": "s1",
        "open_id": "ou_1",
    }).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhook/lark/card",
        content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body_data = resp.json()
    assert body_data["ok"] is True
    bind_doc_service.renew.assert_called_once_with(session_id="s1")


def test_card_non_renew_action_does_not_call_renew(client_with_bind_doc_service):
    client, bind_doc_service = client_with_bind_doc_service
    body = json.dumps({"action": "approve", "approval_id": "a1",
                       "open_id": "ou_1"}).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhook/lark/card",
        content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    # 非 renew_bind action 不应触发 renew
    bind_doc_service.renew.assert_not_called()


# === Phase 14：research_writeback 分支 ===

@pytest.fixture
def client_with_broker():
    from orchestrator.approval_broker import ApprovalBroker
    broker = ApprovalBroker()
    client = TestClient(create_app(
        secret="phase2-secret",
        orchestrator=object(),
        approval_broker=broker,
    ))
    return client, broker


def _post_card(client, payload: dict):
    body = json.dumps(payload).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook/lark/card",
        content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )


def test_research_writeback_decide_reaches_broker(client_with_broker):
    """首次点击：决策写入 broker，research 线程 wait 能取到。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "decided"}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_research_writeback_duplicate_click_rejected(client_with_broker):
    """重复点击：幂等拒绝，首个决策不被覆盖。"""
    client, broker = client_with_broker
    _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                        "decision": "approve", "open_id": "ou_1"})
    resp = _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                               "decision": "deny", "open_id": "ou_2"})
    assert resp.json() == {"ok": False, "status": "already_handled"}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_research_writeback_without_broker_not_configured():
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
    ))
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.json()["ok"] is False
    assert "not configured" in resp.json()["reason"]
