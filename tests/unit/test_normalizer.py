"""Webhook payload → IncomingMessage 归一化测试。"""
import pytest

from gateway.normalizer import NormalizeError, normalize_im_event, parse_bind_doc_cmd


def _payload(text: str, msg_type: str = "text", mentions: list | None = None) -> dict:
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
