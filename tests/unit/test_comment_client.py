"""CommentClient 单测：SDK 原始请求模式 + 官方结构→扁平 dict 适配（ADR-0032）。"""
import json
from unittest.mock import MagicMock

import pytest

from feishu_adapter.comment_client import CommentClient, _extract_text, _to_flat


def _fake_response(payload: dict) -> MagicMock:
    """构造 sdk_client.request 返回的伪 RawResponse。"""
    resp = MagicMock()
    resp.raw.content = json.dumps(payload).encode("utf-8")
    return resp


def _sdk_list_returning(items: list) -> MagicMock:
    """sdk_client.request 返回官方 list 结构的伪响应。"""
    sdk = MagicMock()
    sdk.request.return_value = _fake_response({"code": 0, "data": {"items": items}})
    return sdk


class _FakeLimiter:
    """测试用限流器（直通）。"""

    def wait(self) -> None:
        pass


def _official_item(cid="c1", text="导师：结论要补统计检验", replies=None,
                   solved=False):
    """构造官方 list API 的单条评论 JSON（正文在 replies[0]）。"""
    return {
        "comment_id": cid, "user_id": "ou_teacher", "is_solved": solved,
        "reply_list": {"replies": [
            {"reply_id": f"{cid}_r0", "user_id": "ou_teacher",
             "content": {"elements": [{"type": "text",
                                       "text_run": {"text": text}}]}},
        ] + (replies or [])},
    }


def test_list_comments_adapts_official_structure():
    """官方结构 → 下游扁平 dict：root 正文取 replies[0]，replies[1:] 为回复。"""
    item = _official_item(replies=[
        {"reply_id": "c1_r1", "user_id": "ou_student",
         "content": {"elements": [{"type": "text",
                                   "text_run": {"text": "已补充"}}]}},
    ])
    client = CommentClient(sdk_client=_sdk_list_returning([item]),
                           rate_limiter=_FakeLimiter())
    out = client.list_comments(doc_id="doccnX")
    assert out == [{
        "comment_id": "c1", "user_id": "ou_teacher", "user_name": "",
        "text": "导师：结论要补统计检验", "resolved": False, "block_id": None,
        "replies": [{"reply_id": "c1_r1", "user_id": "ou_student",
                     "user_name": "", "text": "已补充"}],
    }]


def test_list_comments_multi_text_elements_concat():
    """正文多个 text 片段拼接；非文本片段跳过；无后续回复时 replies=[]。"""
    item = _official_item()
    item["reply_list"]["replies"][0]["content"]["elements"] = [
        {"type": "text", "text_run": {"text": "第一段"}},
        {"type": "person", "user_id": "ou_x"},
        {"type": "text", "text_run": {"text": "第二段"}},
    ]
    client = CommentClient(sdk_client=_sdk_list_returning([item]),
                           rate_limiter=_FakeLimiter())
    out = client.list_comments(doc_id="d")
    assert out[0]["text"] == "第一段第二段"
    assert out[0]["replies"] == []


def test_list_comments_api_error_raises():
    """code != 0 抛 FeishuAgentError（携带错误码）。"""
    from shared.errors import FeishuAgentError

    sdk = MagicMock()
    sdk.request.return_value = _fake_response({"code": 1064030, "msg": "denied"})
    client = CommentClient(sdk_client=sdk, rate_limiter=_FakeLimiter())
    with pytest.raises(FeishuAgentError, match="1064030"):
        client.list_comments(doc_id="d")


def test_reply_comment_posts_text_element():
    """reply_comment 用纯文本 element POST 到 replies 接口。"""
    sdk = MagicMock()
    sdk.request.return_value = _fake_response(
        {"code": 0, "data": {"reply": {"reply_id": "c1_r9"}}})
    client = CommentClient(sdk_client=sdk, rate_limiter=_FakeLimiter())
    out = client.reply_comment(file_token="doccnX", comment_id="c1",
                               text="已按评论修改")
    assert out == {"reply_id": "c1_r9"}
    req = sdk.request.call_args.args[0]
    assert "comments/c1/replies" in req.uri
    assert req.body == {"content": {"elements": [
        {"type": "text", "text_run": {"text": "已按评论修改"}}]}}


def test_extract_text_none_safe():
    """_extract_text 对缺字段的 reply 安全返回空串。"""
    assert _extract_text(MagicMock(spec=[])) == ""


def test_to_flat_missing_reply_list():
    """无 reply_list 的评论：text 空串不炸，其余字段正常。"""

    class _Empty:
        comment_id = "c9"
        user_id = "ou_a"
        is_solved = True

    out = _to_flat(_Empty())
    assert out["text"] == "" and out["resolved"] is True
    assert out["comment_id"] == "c9"
