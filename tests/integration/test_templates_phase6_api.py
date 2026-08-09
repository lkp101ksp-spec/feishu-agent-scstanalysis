from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_client():
    version_service = MagicMock()
    version_service.list_versions.return_value = ["v1", "v2"]
    version_service.rollback.return_value = None
    share_service = MagicMock()
    share_service.list_for_chat.return_value = ["t1"]
    share_service.share_to_chat.return_value = None
    share_service.can_access.return_value = True
    hot_loader = MagicMock()
    hot_loader.upload.return_value = "t_new"
    return (
        TestClient(create_app(
            secret="phase2-secret", orchestrator=object(),
            version_service=version_service,
            share_service=share_service,
            hot_loader=hot_loader,
        )),
        version_service, share_service, hot_loader,
    )


def test_list_versions():
    c, vs, _, _ = _make_client()
    resp = c.get("/templates/t_1/versions")
    assert resp.status_code == 200
    assert resp.json()["versions"] == ["v1", "v2"]


def test_rollback():
    c, vs, _, _ = _make_client()
    resp = c.post("/templates/t_1/rollback",
                   json={"version_number": 2, "caller_open_id": "ou_1"})
    assert resp.status_code == 200
    vs.rollback.assert_called_once()


def test_rollback_permission_error():
    c, vs, _, _ = _make_client()
    vs.rollback.side_effect = PermissionError("not owner")
    resp = c.post("/templates/t_1/rollback",
                   json={"version_number": 2, "caller_open_id": "ou_2"})
    assert resp.status_code == 403


def test_share_to_chat():
    c, _, ss, _ = _make_client()
    resp = c.post("/templates/t_1/share",
                   json={"chat_id": "chat_1", "caller_open_id": "ou_1"})
    assert resp.status_code == 200
    ss.share_to_chat.assert_called_once()


def test_list_for_chat():
    c, _, ss, _ = _make_client()
    resp = c.get("/templates/chat/chat_1")
    assert resp.status_code == 200
    assert resp.json()["templates"] == ["t1"]


def test_admin_upload_tool():
    c, _, _, hl = _make_client()
    body = {
        "name": "reverse_complement",
        "code": "def handle(seq): return {'rc': seq[::-1]}",
        "parameters": {"type": "object"},
        "risk_level": "L0_read",
        "actor_open_id": "admin_1",
    }
    resp = c.post("/admin/tools/upload", json=body)
    assert resp.status_code == 200
    assert resp.json()["name"] == "t_new"


def test_admin_upload_blocked_by_ast():
    c, _, _, hl = _make_client()
    from shared.errors import ToolBlockedError
    hl.upload.side_effect = ToolBlockedError("eval blocked")
    body = {
        "name": "bad", "code": "def handle(): return eval('1')",
        "parameters": {"type": "object"}, "risk_level": "L0_read",
        "actor_open_id": "admin_1",
    }
    resp = c.post("/admin/tools/upload", json=body)
    assert resp.status_code == 400