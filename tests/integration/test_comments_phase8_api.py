"""Phase 8 T8: 评论同步/动作 API 集成测试。"""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_client():
    sync = MagicMock()
    sync.sync.return_value = {"fetched": 3, "new": 3, "updated": 0}
    sync.list_stored.return_value = ["c1", "c2", "c3"]
    action = MagicMock()
    action.apply.return_value = {
        "applied": 1, "skipped": 1, "failed": 0, "details": [],
    }
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
        comment_sync_service=sync,
        comment_action_service=action,
    ))
    return client, sync, action


def test_sync_comments():
    c, sync, _ = _make_client()
    resp = c.post("/comments/doc_1/sync")
    assert resp.status_code == 200
    assert resp.json() == {"fetched": 3, "new": 3, "updated": 0}
    sync.sync.assert_called_once_with(doc_id="doc_1")


def test_stored_comments_with_block_filter():
    c, sync, _ = _make_client()
    resp = c.get("/comments/doc_1/stored", params={"block_id": "b1"})
    assert resp.status_code == 200
    assert resp.json()["comments"] == ["c1", "c2", "c3"]
    sync.list_stored.assert_called_once_with(doc_id="doc_1", block_id="b1")


def test_apply_comment_actions():
    c, _, action = _make_client()
    resp = c.post("/comments/doc_1/apply-actions",
                  json={"caller_open_id": "ou_1"})
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1
    action.apply.assert_called_once_with(doc_id="doc_1", caller_open_id="ou_1")
