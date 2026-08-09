from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_client():
    comment_service = MagicMock()
    comment_service.fetch_thread.return_value = "评论列表：\n- U: x"
    search_service = MagicMock()
    search_service.search.return_value = ["t1", "t2"]
    public_service = MagicMock()
    public_service.submit_for_review.return_value = None
    public_service.approve.return_value = None
    public_service.reject.return_value = None
    public_service.list_public.return_value = ["t1"]
    fork_service = MagicMock()
    fork_service.fork_from_public.return_value = "new_t"
    fork_service.list_forks.return_value = ["t2"]
    return (
        TestClient(create_app(
            secret="phase2-secret", orchestrator=object(),
            comment_service=comment_service,
            search_service=search_service,
            public_service=public_service,
            fork_service=fork_service,
        )),
        comment_service, search_service, public_service, fork_service,
    )


def test_fetch_comments():
    c, cs, _, _, _ = _make_client()
    resp = c.get("/comments/doc_1")
    assert resp.status_code == 200
    assert "评论列表" in resp.json()["text"]


def test_fetch_comments_with_block():
    c, cs, _, _, _ = _make_client()
    resp = c.get("/comments/doc_1", params={"block_id": "b1"})
    cs.fetch_thread.assert_called_with(doc_id="doc_1", block_id="b1")


def test_search_templates():
    c, _, ss, _, _ = _make_client()
    resp = c.get("/templates/search", params={"q": "blast"})
    assert resp.status_code == 200
    assert resp.json()["results"] == ["t1", "t2"]


def test_search_with_scope():
    c, _, ss, _, _ = _make_client()
    resp = c.get("/templates/search", params={"q": "x", "scope": "public"})
    ss.search.assert_called_with(query="x", scope="public",
                                  owner_open_id=None,
                                  limit=20, offset=0)


def test_submit_public_template():
    c, _, _, ps, _ = _make_client()
    resp = c.post("/templates/t_1/submit-public",
                   json={"caller_open_id": "ou_1"})
    assert resp.status_code == 200
    ps.submit_for_review.assert_called_once()


def test_admin_review_approve():
    c, _, _, ps, _ = _make_client()
    resp = c.post("/admin/templates/t_1/review",
                   json={"action": "approve", "admin_open_id": "admin_1",
                         "note": "good"})
    assert resp.status_code == 200
    ps.approve.assert_called_once()


def test_admin_review_reject_with_reason():
    c, _, _, ps, _ = _make_client()
    resp = c.post("/admin/templates/t_1/review",
                   json={"action": "reject", "admin_open_id": "admin_1",
                         "reason": "不完整"})
    assert resp.status_code == 200
    ps.reject.assert_called_once_with(
        template_id="t_1", actor_open_id="admin_1", reason="不完整",
    )


def test_list_public_templates():
    c, _, _, ps, _ = _make_client()
    resp = c.get("/templates/public")
    assert resp.status_code == 200
    assert resp.json()["templates"] == ["t1"]


def test_fork_template():
    c, _, _, _, fs = _make_client()
    resp = c.post("/templates/t_1/fork",
                   json={"caller_open_id": "ou_1"})
    assert resp.status_code == 200
    assert resp.json()["new_template_id"] == "new_t"


def test_list_forks():
    c, _, _, _, fs = _make_client()
    resp = c.get("/templates/t_1/forks")
    assert resp.status_code == 200
    assert resp.json()["forks"] == ["t2"]