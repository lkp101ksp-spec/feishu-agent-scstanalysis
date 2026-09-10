"""Phase D：DocAdapter.create_document / grant_doc_view 封装单测（假 SDK 通道）。"""
import json
from types import SimpleNamespace

import lark_oapi as lark
import pytest

from feishu_adapter.client import LarkCLIError
from feishu_adapter.doc_adapter import DocAdapter


def _adapter(payload: dict, captured: dict) -> DocAdapter:
    """构造假 SDK 通道的 DocAdapter；request 捕获 req 并回指定 payload。"""
    def _fake_request(req):
        captured["method"] = req.http_method
        captured["uri"] = req.uri
        captured["body"] = req.body
        return SimpleNamespace(raw=SimpleNamespace(
            content=json.dumps(payload).encode("utf-8")))
    return DocAdapter(sdk_client=SimpleNamespace(request=_fake_request))


def test_create_document_returns_id_and_posts_title():
    captured: dict = {}
    ad = _adapter({"code": 0, "data": {"document": {"document_id": "doc_123"}}},
                  captured)
    doc_id = ad.create_document("分析报告", folder_token="fld_1")
    assert doc_id == "doc_123"
    assert captured["method"] == lark.HttpMethod.POST
    assert captured["uri"] == "/open-apis/docx/v1/documents"
    assert captured["body"] == {"title": "分析报告", "folder_token": "fld_1"}


def test_create_document_without_folder_omits_key():
    captured: dict = {}
    ad = _adapter({"code": 0, "data": {"document": {"document_id": "d"}}},
                  captured)
    ad.create_document("t")
    assert "folder_token" not in captured["body"]


def test_create_document_api_error_raises():
    ad = _adapter({"code": 1770001, "msg": "invalid param"}, {})
    with pytest.raises(LarkCLIError):
        ad.create_document("t")


def test_create_document_empty_id_raises():
    ad = _adapter({"code": 0, "data": {"document": {}}}, {})
    with pytest.raises(LarkCLIError):
        ad.create_document("t")


def test_grant_doc_view_posts_member():
    captured: dict = {}
    ad = _adapter({"code": 0, "data": {}}, captured)
    ad.grant_doc_view("doc_123", "ou_1")
    assert captured["method"] == lark.HttpMethod.POST
    assert captured["uri"] == (
        "/open-apis/drive/v1/permissions/doc_123/members?type=docx")
    assert captured["body"] == {
        "member_type": "openid", "member_id": "ou_1", "perm": "view"}


def test_grant_doc_view_api_error_raises():
    ad = _adapter({"code": 1061002, "msg": "no permission"}, {})
    with pytest.raises(LarkCLIError):
        ad.grant_doc_view("doc_123", "ou_1")


def test_create_document_requires_sdk():
    with pytest.raises(LarkCLIError):
        DocAdapter().create_document("t")
