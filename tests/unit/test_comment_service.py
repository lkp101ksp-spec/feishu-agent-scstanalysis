from unittest.mock import MagicMock

from orchestrator.templates.comment_service import CommentService


def _make():
    client = MagicMock()
    return CommentService(client=client), client


def test_fetch_thread_returns_no_comments_text():
    svc, client = _make()
    client.list_comments.return_value = []
    text = svc.fetch_thread(doc_id="doc_1")
    assert text == "（无评论）"


def test_fetch_thread_renders_comments_and_replies():
    svc, client = _make()
    client.list_comments.return_value = [
        {"user_name": "张三", "text": "Hi", "replies": [
            {"user_name": "李四", "text": "@张三 已补充"},
        ]},
    ]
    text = svc.fetch_thread(doc_id="doc_1")
    assert "张三" in text
    assert "李四" in text


def test_fetch_thread_filters_by_block_id():
    svc, client = _make()
    client.list_block_comments.return_value = [
        {"user_name": "U", "text": "x", "replies": []},
    ]
    text = svc.fetch_thread(doc_id="doc_1", block_id="b1")
    client.list_block_comments.assert_called_once_with(
        doc_id="doc_1", block_id="b1")
    assert "x" in text


def test_fetch_thread_handles_anonymous():
    svc, client = _make()
    client.list_comments.return_value = [
        {"user_name": None, "text": "anon", "replies": []},
    ]
    text = svc.fetch_thread(doc_id="doc_1")
    assert "匿名" in text


def test_fetch_thread_empty_replies():
    svc, client = _make()
    client.list_comments.return_value = [
        {"user_name": "U", "text": "x", "replies": []},
    ]
    text = svc.fetch_thread(doc_id="doc_1")
    assert "↳" not in text
