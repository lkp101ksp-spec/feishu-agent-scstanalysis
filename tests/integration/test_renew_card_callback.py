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
    """内存 SQLite（含 dw1 → requested_by=ou_1）+ 真 ApprovalBroker。

    Phase 15 T1 起回调需查 doc_writes.requested_by 做 operator 校验，
    session_factory 必须注入，避免测试连真实 PG。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from orchestrator.approval_broker import ApprovalBroker
    from persistence.models import Base
    from persistence.repositories.doc_write_repo import DocWriteRepo

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    s = factory()
    try:
        DocWriteRepo(s).create_pending(
            doc_write_id="dw1", task_id="t1", doc_id="doc1",
            requested_by="ou_1", approval_mode="card_confirm",
            payload_text="preview",
        )
        s.commit()
    finally:
        s.close()

    broker = ApprovalBroker()
    app = create_app(
        secret="phase2-secret",
        orchestrator=object(),
        approval_broker=broker,
    )
    app.state.session_factory = factory
    client = TestClient(app)
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
    """发起者首次点击：决策写入 broker，research 线程 wait 能取到。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "decided",
                           "decision": "approve"}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_research_writeback_duplicate_click_rejected(client_with_broker):
    """发起者重复点击：幂等拒绝，首个决策不被覆盖。"""
    client, broker = client_with_broker
    _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                        "decision": "approve", "open_id": "ou_1"})
    resp = _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                               "decision": "deny", "open_id": "ou_1"})
    assert resp.json() == {"ok": False, "status": "already_handled",
                           "decision": ""}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_research_writeback_non_owner_forbidden(client_with_broker):
    """Phase 15 T1：非发起者点击 → forbidden，决策不进 broker。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_other",
    })
    assert resp.json() == {"ok": False, "status": "forbidden"}
    # broker 未收到决策（wait 超时返回 None）
    assert broker.wait("dw1", timeout=0.1) is None


def test_research_writeback_unknown_doc_write_passes(client_with_broker):
    """row 缺失（重启后孤儿/未知 id）：owner 查不到不拦截，走 broker 原逻辑。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw_unknown",
        "decision": "deny", "open_id": "ou_anyone",
    })
    assert resp.json() == {"ok": True, "status": "decided", "decision": "deny"}
    assert broker.wait("dw_unknown", timeout=0.1) == "deny"


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


# === Phase 17：node_l2_approval 分支（write_doc 节点审批） ===

def test_node_l2_approval_decide_reaches_broker(client_with_broker):
    """发起者点同意：决策写入 broker（同一分支逻辑，action 不同）。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "node_l2_approval", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.json() == {"ok": True, "status": "decided",
                           "decision": "approve"}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_node_l2_approval_non_owner_forbidden(client_with_broker):
    """非发起者点击 node_l2_approval → forbidden（owner 校验复用）。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "node_l2_approval", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_other",
    })
    assert resp.json() == {"ok": False, "status": "forbidden"}
    assert broker.wait("dw1", timeout=0.1) is None
