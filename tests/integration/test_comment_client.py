import httpx
import pytest
import respx

from feishu_adapter.comment_client import CommentClient


class NoWaitLimiter:
    def wait(self):
        pass


@pytest.fixture
def client():
    return CommentClient(
        base_url="https://example.feishu.cn", api_token="t",
        rate_limiter=NoWaitLimiter(),
    )


@respx.mock
def test_list_comments(client):
    respx.get(
        "https://example.feishu.cn/open-apis/docx/v1/documents/doc_1/comments"
    ).mock(return_value=httpx.Response(200, json={
        "items": [
            {"id": "c1", "user_name": "张三", "text": "Hi",
             "block_id": "b1", "replies": []},
        ]
    }))
    items = client.list_comments(doc_id="doc_1")
    assert len(items) == 1
    assert items[0]["text"] == "Hi"


@respx.mock
def test_list_block_comments_filters(client):
    respx.get(
        "https://example.feishu.cn/open-apis/docx/v1/documents/doc_1/comments"
    ).mock(return_value=httpx.Response(200, json={
        "items": [
            {"id": "c1", "block_id": "b1", "text": "x"},
            {"id": "c2", "block_id": "b2", "text": "y"},
        ]
    }))
    out = client.list_block_comments(doc_id="doc_1", block_id="b1")
    assert len(out) == 1
    assert out[0]["id"] == "c1"


@respx.mock
def test_list_comments_empty(client):
    respx.get(
        "https://example.feishu.cn/open-apis/docx/v1/documents/doc_1/comments"
    ).mock(return_value=httpx.Response(200, json={"items": []}))
    items = client.list_comments(doc_id="doc_1")
    assert items == []


@respx.mock
def test_list_comments_raises_on_error(client):
    respx.get(
        "https://example.feishu.cn/open-apis/docx/v1/documents/doc_1/comments"
    ).mock(return_value=httpx.Response(500, json={"error": "x"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.list_comments(doc_id="doc_1")


@respx.mock
def test_list_comments_with_replies(client):
    respx.get(
        "https://example.feishu.cn/open-apis/docx/v1/documents/doc_1/comments"
    ).mock(return_value=httpx.Response(200, json={
        "items": [
            {"id": "c1", "user_name": "U1", "text": "Q",
             "block_id": "b1",
             "replies": [{"user_name": "U2", "text": "A"}]},
        ]
    }))
    items = client.list_comments(doc_id="doc_1")
    assert len(items[0]["replies"]) == 1
