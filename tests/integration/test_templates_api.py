from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_service():
    svc = MagicMock()
    svc.get.return_value = None
    svc.list_by_owner.return_value = []
    return svc


@pytest.fixture
def client():
    svc = _make_service()
    return TestClient(create_app(
        secret="phase2-secret",
        orchestrator=object(),
        template_service=svc,
    )), svc


def test_create_block_endpoint(client):
    c, svc = client
    svc.create_block.return_value = "t_1"
    body = {
        "owner_open_id": "ou_1",
        "name": "std",
        "blocks": [
            {"type": "heading", "level": 2, "text": "Hi"},
            {"type": "text", "text": "x"},
        ],
        "description": "d",
    }
    resp = c.post("/templates/block", json=body)
    assert resp.status_code == 200
    assert resp.json()["template_id"] == "t_1"


def test_list_endpoint(client):
    c, svc = client
    t1 = MagicMock(template_id="t1")
    t2 = MagicMock(template_id="t2")
    svc.list_by_owner.return_value = [t1, t2]
    resp = c.get("/templates/", params={"owner_open_id": "ou_1"})
    assert resp.status_code == 200
    assert resp.json()["templates"] == ["t1", "t2"]


def test_render_endpoint(client):
    c, svc = client
    tpl = MagicMock(type="block", template_id="t_1")
    svc.get.return_value = tpl
    svc.render_block.return_value = [
        MagicMock(type="heading", model_dump=lambda: {"type": "heading",
                                                       "level": 2, "text": "BRCA1"}),
    ]
    body = {"params": {"gene": "BRCA1"}}
    resp = c.post("/templates/t_1/render", json=body)
    assert resp.status_code == 200
    svc.render_block.assert_called_once()


def test_delete_endpoint_requires_owner(client):
    c, svc = client
    svc.delete.side_effect = PermissionError("not owner")
    resp = c.delete("/templates/t_1", params={"caller_open_id": "ou_2"})
    assert resp.status_code == 403
