"""Phase 9 T7: search-v2 / diff moved 路由集成测试。"""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_client():
    us = MagicMock()
    us.search.return_value = [{
        "template_id": "t1", "name": "blast 流程",
        "score": 2.5, "tags": ["bio"], "favorite_count": 3,
    }]
    ds = MagicMock()
    ds.diff.return_value = {
        "template_id": "t1", "v_a": 1, "v_b": 2,
        "blocks": {"added": [], "removed": [], "changed": [],
                    "moved": [{"index_a": 0, "index_b": 1, "type": "heading"}]},
        "steps": {"added": [], "removed": [], "changed": [], "moved": []},
        "meta": {"name_changed": False, "name_a": "n", "name_b": "n",
                  "description_changed": False},
    }
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
        unified_search_service=us, diff_service=ds,
    ))
    return client, us, ds


def test_search_v2_route():
    c, us, _ = _make_client()
    resp = c.get("/templates/search-v2",
                 params={"q": "blast", "tag": "bio", "scope": "public"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["template_id"] == "t1"
    assert results[0]["favorite_count"] == 3
    us.search.assert_called_once_with(
        query="blast", tag="bio", scope="public", limit=20, offset=0)


def test_search_v2_not_configured_empty():
    c = TestClient(create_app(secret="s", orchestrator=object()))
    resp = c.get("/templates/search-v2", params={"q": "x"})
    assert resp.status_code == 200
    assert resp.json() == {"results": []}


def test_diff_route_has_moved_key():
    c, _, ds = _make_client()
    resp = c.get("/templates/t1/diff", params={"v_a": 1, "v_b": 2})
    assert resp.status_code == 200
    assert resp.json()["blocks"]["moved"][0]["type"] == "heading"


def test_search_v2_tag_param_forwarded_raw():
    """路由透传原始 tag（归一化在 service 层）。"""
    c, us, _ = _make_client()
    c.get("/templates/search-v2", params={"q": "x", "tag": "  BIO "})
    assert us.search.call_args.kwargs["tag"] == "  BIO "
