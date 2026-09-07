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


def test_im_send_card_sdk_dict_header_passthrough():
    """header 为完整 dict 时原样透传（/model、/code 审批卡形态）。"""
    sdk = _im_sdk(message_id="om_card2")
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    header = {"title": {"tag": "plain_text", "content": "/model 模型切换"}}
    adapter.send_card("oc_x", {"header": header, "elements": []})
    req = sdk.im.v1.message.create.call_args.args[0]
    card = json.loads(req.request_body.content)
    assert card["header"] == header  # 不再 str(dict) 渲染 repr


# === Phase 39：update_card（PATCH 原地更新，进度卡 v2） ===


def _im_sdk_with_patch(patch_success: bool = True, code: int = 0, msg: str = "ok"):
    """mock sdk client：im.v1.message.patch 返回指定结果。"""
    sdk = _im_sdk()
    sdk.im.v1.message.patch.return_value = SimpleNamespace(
        success=lambda: patch_success, code=code, msg=msg)
    return sdk


def test_im_update_card_sdk_patches_message():
    """SDK 路径：PATCH 指定 message_id，content 为归一化卡片 JSON。"""
    sdk = _im_sdk_with_patch()
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    adapter.update_card("om_progress", {"header": "代码任务进行中 · step 3",
                                        "elements": []})
    req = sdk.im.v1.message.patch.call_args.args[0]
    assert req.message_id == "om_progress"
    card = json.loads(req.request_body.content)
    assert card["header"]["title"]["content"] == "代码任务进行中 · step 3"
    assert card["config"]["wide_screen_mode"] is True


def test_im_update_card_sdk_failure_raises():
    sdk = _im_sdk_with_patch(patch_success=False, code=230002, msg="msg gone")
    adapter = IMAdapter(cli=MagicMock(), sdk_client=sdk)
    with pytest.raises(LarkCLIError, match="230002"):
        adapter.update_card("om_x", {"header": "t", "elements": []})


def test_im_update_card_cli_path_not_supported():
    """CLI 路径不支持原地更新：抛 NotImplementedError（调用方回退 v1）。"""
    adapter = IMAdapter(cli=MagicMock())
    with pytest.raises(NotImplementedError):
        adapter.update_card("om_x", {"header": "t", "elements": []})


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


def test_doc_render_blocks_sdk_image_path_uses_insert_flow():
    """path 图片块走三步插入（空块→上传→replace），不进批量 children。"""
    from unittest.mock import patch

    sdk = _doc_sdk({"code": 0, "data": {"children": []}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    with patch.object(adapter, "insert_doc_image",
                      return_value="blk_img_1") as ins:
        adapter.render_blocks("doc_x", [
            TextBlock(text="before"),
            ImageBlock(path=r"D:\ws\umap.png", alt="umap"),
            TextBlock(text="after"),
        ])
    ins.assert_called_once_with("doc_x", r"D:\ws\umap.png")
    # 文本块照常批量 children（分两批：image 前 flush + 末批）
    all_types = [
        b["block_type"]
        for c in sdk.request.call_args_list
        for b in c.args[0].body["children"]
    ]
    assert all_types == [2, 2]  # before/after 两个文本块


def test_doc_render_blocks_sdk_image_insert_failure_not_fatal():
    """单图插入失败：记日志跳过，文本写回不受影响。"""
    from unittest.mock import patch

    sdk = _doc_sdk({"code": 0, "data": {"children": []}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    with patch.object(adapter, "insert_doc_image",
                      side_effect=RuntimeError("drive down")):
        adapter.render_blocks("doc_x", [
            TextBlock(text="t1"),
            ImageBlock(path=r"D:\ws\umap.png"),
        ])
    sdk.request.assert_called_once()  # 仅文本批，未因图失败中断


def test_doc_insert_doc_image_three_steps():
    """insert_doc_image 三步：空 image block → 上传(parent=block) → PATCH。"""
    from unittest.mock import patch

    sdk = _doc_sdk({"code": 0, "data": {
        "children": [{"block_id": "blk_img_9", "block_type": 27}]}})
    adapter = DocAdapter(cli=MagicMock(), sdk_client=sdk,
                         rate_limiter=NoWaitLimiter())
    with patch.object(adapter, "upload_doc_image",
                      return_value="boxbckFT1") as up:
        block_id = adapter.insert_doc_image("doc_x", r"D:\ws\umap.png")
    assert block_id == "blk_img_9"
    # ② 上传 parent_node = 空 image block 的 block_id（非 doc_id）
    up.assert_called_once_with("blk_img_9", r"D:\ws\umap.png")
    # ① 空 image 块（image 必须空对象——带 token 会 1770001）
    first_req = sdk.request.call_args_list[0].args[0]
    assert first_req.body["children"] == [{"block_type": 27, "image": {}}]
    # ③ 最后一次调用是 PATCH replace_image(token)
    last_req = sdk.request.call_args.args[0]
    assert "PATCH" in str(last_req.http_method)
    assert last_req.body == {"replace_image": {"token": "boxbckFT1"}}


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
