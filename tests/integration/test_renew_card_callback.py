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
