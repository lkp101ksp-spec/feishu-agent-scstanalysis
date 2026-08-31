"""Phase 19: 标签推荐 API 集成测试（GET /templates/{id}/tag-suggestions）。"""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from gateway.app import create_app


def _client(service):
    app = create_app(secret="s", orchestrator=MagicMock(),
                     tag_recommend_service=service)
    return TestClient(app)


def test_tag_suggestions_ok():
    svc = MagicMock()
    svc.suggest.return_value = ["qpcr", "primer"]
    with _client(svc) as client:
        resp = client.get("/templates/t1/tag-suggestions", params={"limit": 2})
    assert resp.status_code == 200
    assert resp.json() == {"template_id": "t1",
                           "suggestions": ["qpcr", "primer"]}
    svc.suggest.assert_called_once_with(template_id="t1", limit=2)


def test_tag_suggestions_not_found_404():
    svc = MagicMock()
    svc.suggest.side_effect = ValueError("template t_missing not found")
    with _client(svc) as client:
        resp = client.get("/templates/t_missing/tag-suggestions")
    assert resp.status_code == 404


def test_tag_suggestions_service_not_configured_503():
    app = create_app(secret="s", orchestrator=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/templates/t1/tag-suggestions")
    assert resp.status_code == 503


def test_tag_suggestions_default_limit():
    svc = MagicMock()
    svc.suggest.return_value = []
    with _client(svc) as client:
        resp = client.get("/templates/t1/tag-suggestions")
    assert resp.status_code == 200
    svc.suggest.assert_called_once_with(template_id="t1", limit=5)
