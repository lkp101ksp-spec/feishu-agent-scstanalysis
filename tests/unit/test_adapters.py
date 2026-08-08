"""飞书适配层测试（mock lark-cli）。"""
from unittest.mock import patch

import pytest

from feishu_adapter.base_projection_adapter import BaseProjectionAdapter
from feishu_adapter.client import LarkCLIError
from feishu_adapter.doc_adapter import DocAdapter
from feishu_adapter.im_adapter import IMAdapter


def test_im_reply_returns_message_id():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"message_id": "om_xxx"}):
        msg_id = IMAdapter().reply(chat_id="oc_x", text="hi")
    assert msg_id == "om_xxx"


def test_im_send_with_custom_type():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"message_id": "om_y"}) as mock_run:
        IMAdapter().send(
            receive_id="ou_abc",
            receive_id_type="open_id",
            msg_type="text",
            content="hello",
        )
    # 验证调用参数正确
    args = mock_run.call_args.args[0]
    assert "open_id" in args
    assert "ou_abc" in args
    assert "text" in args


def test_doc_append_plain_text_returns_block_id():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"block_id": "blk_xxx"}):
        block_id = DocAdapter().append_plain_text(doc_id="doc_x", text="hello")
    assert block_id == "blk_xxx"


def test_doc_get_block_tree_returns_list():
    fake_blocks = [{"id": "b1"}, {"id": "b2"}]
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"blocks": fake_blocks}):
        blocks = DocAdapter().get_block_tree(doc_id="doc_x")
    assert blocks == fake_blocks


def test_base_projection_returns_true_on_success():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"record_id": "rec_1"}):
        result = BaseProjectionAdapter(app_token="bascn_x").project_task(
            task_id="t1", status="success", reply_text="ok", error_message=None
        )
    assert result is True


def test_base_projection_swallows_errors_returns_false():
    with patch("feishu_adapter.client.LarkCLI.run", side_effect=LarkCLIError("boom")):
        result = BaseProjectionAdapter(app_token="bascn_x").project_task(
            task_id="t1", status="success", reply_text="ok", error_message=None
        )
    assert result is False


def test_base_projection_truncates_long_reply():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"record_id": "rec_1"}) as mock_run:
        long_text = "x" * 500
        BaseProjectionAdapter(app_token="bascn_x").project_task(
            task_id="t1", status="success", reply_text=long_text, error_message=None
        )
    # 验证截断：reply_text 字段不应超过 200 字符
    fields_args = mock_run.call_args.args[0]
    reply_field = next(f for f in fields_args if f.startswith("reply_text="))
    assert len(reply_field.replace("reply_text=", "")) == 200