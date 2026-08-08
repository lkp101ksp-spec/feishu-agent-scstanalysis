"""FastAPI webhook 端到端测试。"""
import base64
import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


SECRET = "test_secret_key"


def _sign(timestamp: str, body: str, secret: str = SECRET) -> str:
    """按飞书官方算法生成签名。"""
    s = f"{timestamp}\n{secret}\n{body}".encode("utf-8")
    return base64.b64encode(hmac.new(s, digestmod=hashlib.sha256).digest()).decode("utf-8")


def _payload(text: str, msg_type: str = "text") -> str:
    """构造最小可用的 im.message.receive_v1 payload。"""
    import json
    msg = {
        "chat_id": "oc_xxx",
        "message_id": "om_xxx",
        "message_type": msg_type,
        "content": json.dumps({"text": text}),
    }
    return json.dumps({
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx"}},
            "message": msg,
        },
    })


@pytest.fixture
def orchestrator_mock():
    orch = MagicMock()
    orch.process.return_value = {
        "status": "success",
        "task_id": "t_xxx",
        "session_id": "s_xxx",
        "reply_text": "ok",
    }
    return orch


@pytest.fixture
def session_factory_fixture():
    """每次调用返回一个全新的内存 SQLite Session；表结构每次重建。

    同时重置 persistence.engine 模块级缓存，防止之前的测试污染。
    """
    import persistence.engine as engine_mod
    engine_mod._engine = None
    engine_mod._SessionLocal = None

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from persistence.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    def _make():
        return Session()

    return _make


def test_health_returns_ok(orchestrator_mock):
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock)
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_webhook_invalid_signature_returns_401(orchestrator_mock):
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock)
    body = _payload("hi")
    with TestClient(app) as client:
        resp = client.post(
            "/webhook/lark",
            content=body,
            headers={"X-Lark-Request-Timestamp": str(int(time.time())), "X-Lark-Signature": "bogus"},
        )
    assert resp.status_code == 401
    assert orchestrator_mock.process.call_count == 0


def test_webhook_expired_signature_returns_401(orchestrator_mock):
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock)
    body = _payload("hi")
    ts = str(int(time.time()) - 3600)  # 1 小时前
    sig = _sign(ts, body)
    with TestClient(app) as client:
        resp = client.post(
            "/webhook/lark",
            content=body,
            headers={"X-Lark-Request-Timestamp": ts, "X-Lark-Signature": sig},
        )
    assert resp.status_code == 401


def test_webhook_valid_signature_dispatches_to_orchestrator(orchestrator_mock, session_factory_fixture):
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock, session_factory=session_factory_fixture)
    body = _payload("hi")
    ts = str(int(time.time()))
    sig = _sign(ts, body)
    with TestClient(app) as client:
        resp = client.post(
            "/webhook/lark",
            content=body,
            headers={
                "X-Lark-Request-Timestamp": ts,
                "X-Lark-Signature": sig,
                "X-Lark-App-Id": "app_test",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert orchestrator_mock.process.call_count == 1


def test_webhook_duplicate_message_returns_200_no_reprocess(orchestrator_mock, session_factory_fixture):
    """幂等：同一条 webhook 重投应直接返回 200，不再调用 orchestrator.process。"""
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock, session_factory=session_factory_fixture)
    body = _payload("hi")
    ts = str(int(time.time()))
    sig = _sign(ts, body)
    headers = {
        "X-Lark-Request-Timestamp": ts,
        "X-Lark-Signature": sig,
        "X-Lark-App-Id": "app_dup",
    }
    with TestClient(app) as client:
        r1 = client.post("/webhook/lark", content=body, headers=headers)
        r2 = client.post("/webhook/lark", content=body, headers=headers)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"
        assert orchestrator_mock.process.call_count == 1


def test_webhook_non_text_returns_400(orchestrator_mock):
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock)
    body = _payload("hi", msg_type="image")
    ts = str(int(time.time()))
    sig = _sign(ts, body)
    with TestClient(app) as client:
        resp = client.post(
            "/webhook/lark",
            content=body,
            headers={"X-Lark-Request-Timestamp": ts, "X-Lark-Signature": sig},
        )
    assert resp.status_code == 400


def test_webhook_rate_limit_returns_429(orchestrator_mock, session_factory_fixture):
    """超出 rate_limit 时返回 429。"""
    app = create_app(secret=SECRET, orchestrator=orchestrator_mock, rate_per_min=2, session_factory=session_factory_fixture)
    body1 = _payload("hi")
    body2 = _payload("hi")
    body3 = _payload("hi")
    ts = str(int(time.time()))

    sig1 = _sign(ts, body1)
    sig2 = _sign(ts, body2)
    sig3 = _sign(ts, body3)

    headers = {"X-Lark-Request-Timestamp": ts, "X-Lark-Signature": "x"}
    # 用同一个合法 secret 重新签一遍（rate_limit 应在签名校验之后）

    with patch("gateway.app.verify_lark_signature") as mock_verify:
        mock_verify.return_value = True
        with TestClient(app) as client:
            r1 = client.post("/webhook/lark", content=body1, headers={**headers, "X-Lark-Signature": "x", "X-Lark-App-Id": "rate_app"})
            r2 = client.post("/webhook/lark", content=body2, headers={**headers, "X-Lark-Signature": "x", "X-Lark-App-Id": "rate_app"})
            r3 = client.post("/webhook/lark", content=body3, headers={**headers, "X-Lark-Signature": "x", "X-Lark-App-Id": "rate_app"})

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429