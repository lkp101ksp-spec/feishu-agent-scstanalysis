"""Phase 8 T8: diff / 标签 / 收藏 API 集成测试。"""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_client():
    diff = MagicMock()
    diff.diff.return_value = {
        "template_id": "t1", "v_a": 1, "v_b": 2,
        "blocks": {"added": [{"index_b": 2, "type": "code"}],
                    "removed": [], "changed": []},
        "steps": {"added": [], "removed": [], "changed": []},
        "meta": {"name_changed": False, "name_a": "n", "name_b": "n",
                  "description_changed": True},
    }
    tag = MagicMock()
    tag.attach.return_value = "blast"
    tag.detach.return_value = None
    tag.find_by_tag.return_value = ["t1"]
    fav = MagicMock()
    fav.favorite.return_value = None
    fav.unfavorite.return_value = None
    fav.list_favorites.return_value = ["t1", "t2"]
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
        diff_service=diff, tag_service=tag, favorite_service=fav,
    ))
    return client, diff, tag, fav


def test_diff_template():
    c, diff, _, _ = _make_client()
    resp = c.get("/templates/t1/diff", params={"v_a": 1, "v_b": 2})
    assert resp.status_code == 200
    assert resp.json()["blocks"]["added"][0]["type"] == "code"
    diff.diff.assert_called_once_with(template_id="t1", v_a=1, v_b=2)


def test_diff_missing_version_404():
    c, diff, _, _ = _make_client()
    diff.diff.side_effect = ValueError("version 9 of t1 not found")
    resp = c.get("/templates/t1/diff", params={"v_a": 1, "v_b": 9})
    assert resp.status_code == 404


def test_add_tag_normalizes():
    c, _, tag, _ = _make_client()
    resp = c.post("/templates/t1/tags",
                  json={"tag": "  BLAST ", "caller_open_id": "ou_1"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "tag": "blast"}
    tag.attach.assert_called_once_with(
        template_id="t1", tag="  BLAST ", caller_open_id="ou_1")


def test_add_tag_not_owner_403():
    c, _, tag, _ = _make_client()
    tag.attach.side_effect = PermissionError("not owner")
    resp = c.post("/templates/t1/tags",
                  json={"tag": "bio", "caller_open_id": "ou_2"})
    assert resp.status_code == 403


def test_remove_tag():
    c, _, tag, _ = _make_client()
    resp = c.delete("/templates/t1/tags",
                    params={"tag": "bio", "caller_open_id": "ou_1"})
    assert resp.status_code == 200
    tag.detach.assert_called_once_with(
        template_id="t1", tag="bio", caller_open_id="ou_1")


def test_templates_by_tag():
    c, _, tag, _ = _make_client()
    resp = c.get("/templates/by-tag/bio")
    assert resp.status_code == 200
    assert resp.json()["templates"] == ["t1"]
    tag.find_by_tag.assert_called_once_with("bio")


def test_favorite_and_favorites():
    c, _, _, fav = _make_client()
    resp = c.post("/templates/t1/favorite",
                  json={"caller_open_id": "ou_1"})
    assert resp.status_code == 200
    fav.favorite.assert_called_once_with(
        template_id="t1", caller_open_id="ou_1")
    resp = c.get("/templates/favorites/ou_1")
    assert resp.json()["templates"] == ["t1", "t2"]


def test_unfavorite():
    c, _, _, fav = _make_client()
    resp = c.delete("/templates/t1/favorite",
                    params={"caller_open_id": "ou_1"})
    assert resp.status_code == 200
    fav.unfavorite.assert_called_once_with(
        template_id="t1", caller_open_id="ou_1")
