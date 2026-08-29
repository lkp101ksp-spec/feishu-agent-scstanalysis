"""Webhook payload → IncomingMessage 归一化测试。"""
import pytest

from gateway.normalizer import (
    NormalizeError,
    normalize_im_event,
    parse_bind_doc_cmd,
    parse_write_to,
)


def _payload(text: str, msg_type: str = "text", mentions: list | None = None,
             chat_type: str | None = None) -> dict:
    """构造一个最小可用的 im.message.receive_v1 payload。"""
    content = '{"text": "%s"}' % text.replace('"', '\\"')
    msg: dict = {
        "chat_id": "oc_xxx",
        "message_id": "om_xxx",
        "message_type": msg_type,
        "content": content,
    }
    if mentions is not None:
        msg["mentions"] = mentions
    if chat_type is not None:
        msg["chat_type"] = chat_type
    return {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx"}},
            "message": msg,
        },
    }


def test_normalize_minimal_text_message():
    msg = normalize_im_event(_payload("hello"))
    assert msg.message_id == "om_xxx"
    assert msg.chat_id == "oc_xxx"
    assert msg.sender_open_id == "ou_xxx"
    assert msg.text == "hello"
    assert msg.is_bind_doc_cmd is False
    assert msg.bind_doc_id is None


def test_normalize_extracts_chat_type():
    """chat_type 透传：group/p2p 正常提取，缺失时为空串。"""
    assert normalize_im_event(_payload("hi", chat_type="group")).chat_type == "group"
    assert normalize_im_event(_payload("hi", chat_type="p2p")).chat_type == "p2p"
    assert normalize_im_event(_payload("hi")).chat_type == ""


def test_parse_bind_doc_cmd_valid():
    _, doc_id = parse_bind_doc_cmd("/bind-doc doccnABC123")
    assert doc_id == "doccnABC123"


def test_parse_bind_doc_cmd_invalid_returns_none():
    _, doc_id = parse_bind_doc_cmd("hello world")
    assert doc_id is None


def test_parse_bind_doc_cmd_accepts_full_url():
    """完整文档链接自动提取 doc_id token（联调 UX 修正）。"""
    _, doc_id = parse_bind_doc_cmd(
        "/bind-doc https://xxx.feishu.cn/docx/ABCdef123?from=copy")
    assert doc_id == "ABCdef123"


def test_parse_bind_doc_cmd_wiki_url_gets_prefix():
    """wiki 链接提取后加 wiki: 前缀，交由 BindDocService 解析。"""
    _, doc_id = parse_bind_doc_cmd(
        "/bind-doc https://xxx.feishu.cn/wiki/HlTGwdTO4i8VGLkYUnDcgJAxnXb?from=copy")
    assert doc_id == "wiki:HlTGwdTO4i8VGLkYUnDcgJAxnXb"


def test_parse_bind_doc_cmd_with_anchor():
    """@锚点 语法：/bind-doc <链接> @章节标题。"""
    anchor, doc_id = parse_bind_doc_cmd(
        "/bind-doc https://xxx.feishu.cn/docx/ABCdef123 @1 测试")
    assert doc_id == "ABCdef123"
    assert anchor == "1 测试"


def test_normalize_bind_cmd_with_mention_keeps_anchor():
    """群聊 @机器人 + @锚点：按 mention key 精确剥离，锚点保留。"""
    msg = normalize_im_event(_payload(
        "@_user_1 /bind-doc https://xxx.feishu.cn/docx/ABCdef123 @结果章节",
        mentions=[{"key": "@_user_1"}]))
    assert msg.is_bind_doc_cmd is True
    assert msg.bind_doc_id == "ABCdef123"
    assert msg.bind_anchor == "结果章节"


def test_normalize_bind_doc_command_sets_flags():
    msg = normalize_im_event(_payload("/bind-doc doccnABC123"))
    assert msg.is_bind_doc_cmd is True
    assert msg.bind_doc_id == "doccnABC123"
    assert msg.text == "/bind-doc doccnABC123"


def test_parse_write_to_pipe_separator():
    """单行用法：#写到 <锚点> | <正文>，锚点标题可含空格。"""
    anchor, body = parse_write_to("#写到 1 测试 | 帮我记录今天的结论")
    assert anchor == "1 测试"
    assert body == "帮我记录今天的结论"


def test_parse_write_to_newline_separator():
    """多行用法：锚点独占一行，正文跟在换行后（正文含 | 不受影响）。"""
    anchor, body = parse_write_to("#写到 1 测试\n帮我记录 a|b")
    assert anchor == "1 测试"
    assert body == "帮我记录 a|b"


def test_parse_write_to_no_body():
    """语法不完整：只有锚点没正文 → (锚点, "")。"""
    anchor, body = parse_write_to("#写到 1 测试")
    assert anchor == "1 测试"
    assert body == ""


def test_parse_write_to_not_write_to_returns_untouched():
    """非 #写到 开头：原文返回，锚点 None。"""
    anchor, body = parse_write_to("帮我分析这个数据 #tag")
    assert anchor is None
    assert body == "帮我分析这个数据 #tag"


def test_normalize_write_to_strips_prefix_and_sets_anchor():
    """归一化：#写到 前缀剥离，正文干净，write_anchor 生效。"""
    msg = normalize_im_event(_payload("#写到 1 测试 | 帮我记录结论"))
    assert msg.write_anchor == "1 测试"
    assert msg.text == "帮我记录结论"
    assert msg.is_bind_doc_cmd is False


def test_normalize_write_to_with_mention():
    """群聊 @机器人 + #写到：mention 剥离后语法仍识别。"""
    msg = normalize_im_event(_payload(
        "@_user_1 #写到 结果章节 | 记录一下",
        mentions=[{"key": "@_user_1"}]))
    assert msg.write_anchor == "结果章节"
    assert msg.text == "记录一下"


def test_normalize_with_mention_strips_at():
    msg = normalize_im_event(
        _payload("@_user_1 帮我分析", mentions=[{"key": "@_user_1"}])
    )
    assert msg.text == "帮我分析"


def test_normalize_skips_non_text():
    with pytest.raises(NormalizeError) as exc:
        normalize_im_event(_payload("any", msg_type="image"))
    assert "non-text" in str(exc.value).lower()


def test_normalize_missing_event_raises():
    with pytest.raises(NormalizeError):
        normalize_im_event({"event": {}})


def test_normalize_invalid_content_raises():
    bad = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "chat_id": "oc", "message_id": "om", "message_type": "text",
                "content": "not-json",
            },
        },
    }
    with pytest.raises(NormalizeError):
        normalize_im_event(bad)
