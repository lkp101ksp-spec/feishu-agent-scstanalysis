"""飞书适配层 lark-oapi SDK 路径测试（mock sdk client，不发真实请求）。"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from feishu_adapter.client import LarkCLIError
from feishu_adapter.doc_adapter import DocAdapter
from feishu_adapter.im_adapter import IMAdapter
from orchestrator.blocks.schemas import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    QuoteBlock,
    TextBlock,
)


class NoWaitLimiter:
    def wait(self):
        pass


def _im_sdk(message_id: str = "om_sdk", success: bool = True,
            code: int = 0, msg: str = "ok"):
    """构造 mock sdk client：im.v1.message.create 返回指定结果。"""
    sdk = MagicMock()
    resp = SimpleNamespace(
        success=lambda: success, code=code, msg=msg,
        data=SimpleNamespace(message_id=message_id),
    )
    sdk.im.v1.message.create.return_value = resp
    return sdk


def _doc_sdk(payload: dict):
    """构造 mock sdk client：request() 返回 BaseResponse（.raw.content 为 JSON 字节）。"""
    sdk = MagicMock()
    sdk.request.return_value = SimpleNamespace(
        raw=SimpleNamespace(content=json.dumps(payload).encode("utf-8")))
    return sdk


# === IMAdapter SDK 路径 ===

def test_im_reply_sdk_returns_message_id():
    sdk = _im_sdk(message_id="om_real")
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    assert adapter.reply(chat_id="oc_x", text="hi") == "om_real"
    sdk.im.v1.message.create.assert_called_once()


def test_im_reply_sdk_failure_raises():
    sdk = _im_sdk(success=False, code=230001, msg="bot not in chat")
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    with pytest.raises(LarkCLIError, match="230001"):
        adapter.reply(chat_id="oc_x", text="hi")


def test_im_send_sdk_wraps_bare_text():
    sdk = _im_sdk()
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    adapter.send(receive_id="ou_a", receive_id_type="open_id",
                 msg_type="text", content="hello")
    req = sdk.im.v1.message.create.call_args.args[0]
    assert json.loads(req.request_body.content) == {"text": "hello"}


def test_im_send_sdk_keeps_json_text():
    sdk = _im_sdk()
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    adapter.send(receive_id="ou_a", receive_id_type="open_id",
                 msg_type="text", content='{"text": "already"}')
    req = sdk.im.v1.message.create.call_args.args[0]
    assert json.loads(req.request_body.content) == {"text": "already"}


def test_im_send_card_sdk_interactive():
    sdk = _im_sdk(message_id="om_card")
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    msg_id = adapter.send_card("oc_x", {"header": "续期", "elements": []})
    assert msg_id == "om_card"
    req = sdk.im.v1.message.create.call_args.args[0]
    assert req.request_body.msg_type == "interactive"
    card = json.loads(req.request_body.content)
    assert card["header"]["title"]["content"] == "续期"


# === DocAdapter SDK 路径 ===

def test_doc_append_plain_text_sdk_returns_block_id():
    sdk = _doc_sdk({"code": 0, "data": {"children": [{"block_id": "blk_1"}]}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    assert adapter.append_plain_text(doc_id="doc_x", text="hello") == "blk_1"
    req = sdk.request.call_args.args[0]
    assert "/documents/doc_x/blocks/doc_x/children" in req.uri


def test_doc_append_sdk_api_error_raises():
    sdk = _doc_sdk({"code": 1770001, "msg": "permission denied"})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    with pytest.raises(LarkCLIError, match="1770001"):
        adapter.append_plain_text(doc_id="doc_x", text="hello")


def test_doc_get_block_tree_sdk_pagination():
    sdk = MagicMock()
    page1 = {"code": 0, "data": {"items": [{"block_id": "b1"}],
                                 "has_more": True, "page_token": "t2"}}
    page2 = {"code": 0, "data": {"items": [{"block_id": "b2"}],
                                 "has_more": False}}
    sdk.request.side_effect = [
        SimpleNamespace(raw=SimpleNamespace(content=json.dumps(page1).encode())),
        SimpleNamespace(raw=SimpleNamespace(content=json.dumps(page2).encode())),
    ]
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    blocks = adapter.get_block_tree("doc_x")
    assert [b["block_id"] for b in blocks] == ["b1", "b2"]
    assert "page_token=t2" in sdk.request.call_args_list[1].args[0].uri


def test_doc_render_blocks_sdk_numeric_types():
    sdk = _doc_sdk({"code": 0, "data": {"children": []}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    adapter.render_blocks("doc_x", [
        HeadingBlock(level=2, text="Title"),
        TextBlock(text="body"),
        CodeBlock(text="print(1)", language="python"),
        QuoteBlock(text="quoted"),
        ListBlock(items=["a", "b"], ordered=False),
    ])
    body = sdk.request.call_args.args[0].body
    types = [b["block_type"] for b in body["children"]]
    assert types == [4, 2, 14, 15, 12, 12]  # heading2/text/code/quote/2 bullets
    code_block = body["children"][2]
    assert code_block["code"]["style"]["language"] == 54  # python


def test_doc_render_blocks_sdk_fallback_for_media():
    sdk = _doc_sdk({"code": 0, "data": {"children": []}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    adapter.render_blocks("doc_x", [ImageBlock(url="http://x/y.png", alt="pic")])
    body = sdk.request.call_args.args[0].body
    child = body["children"][0]
    assert child["block_type"] == 2  # 图片降级为文本
    assert "http://x/y.png" in child["text"]["elements"][0]["text_run"]["content"]


def test_doc_render_blocks_sdk_batches_over_50():
    sdk = _doc_sdk({"code": 0, "data": {"children": []}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    adapter.render_blocks("doc_x", [TextBlock(text=f"t{i}") for i in range(60)])
    assert sdk.request.call_count == 2  # 50 + 10 两批


def test_doc_resolve_wiki_token_sdk():
    sdk = _doc_sdk({"code": 0, "data": {"node": {
        "obj_type": "docx", "obj_token": "doxcnRealXYZ"}}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    assert adapter.resolve_wiki_token("HlTGw123") == "doxcnRealXYZ"
    req = sdk.request.call_args.args[0]
    assert "wiki/v2/spaces/get_node" in req.uri


def test_doc_resolve_wiki_token_non_docx_raises():
    sdk = _doc_sdk({"code": 0, "data": {"node": {
        "obj_type": "sheet", "obj_token": "shtabc"}}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    with pytest.raises(LarkCLIError, match="不是云文档"):
        adapter.resolve_wiki_token("HlTGw123")


def test_doc_resolve_wiki_token_without_sdk_raises():
    adapter = DocAdapter(cli=MagicMock(), rate_limiter=NoWaitLimiter())
    with pytest.raises(LarkCLIError, match="sdk_client"):
        adapter.resolve_wiki_token("HlTGw123")


def test_doc_append_plain_text_with_index():
    """锚点定位写入：index>=0 时按指定位置插入。"""
    sdk = _doc_sdk({"code": 0, "data": {"children": [{"block_id": "blk_new"}]}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    block_id = adapter.append_plain_text("doc_x", "hi", index=3)
    assert block_id == "blk_new"
    body = sdk.request.call_args.args[0].body
    assert body["index"] == 3


def test_doc_list_root_children():
    sdk = _doc_sdk({"code": 0, "data": {
        "items": [{"block_id": "b0"}, {"block_id": "b1"}],
        "has_more": False}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    children = adapter.list_root_children("doc_x")
    assert [b["block_id"] for b in children] == ["b0", "b1"]
    req = sdk.request.call_args.args[0]
    assert "blocks/doc_x/children" in req.uri
