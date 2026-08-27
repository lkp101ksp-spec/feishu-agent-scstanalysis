"""LLM Router 测试：主成功 / fallback / 全失败。"""
import httpx
import pytest

from orchestrator.llm_router import LLMRouter
from shared.errors import LLMCallError
from shared.schemas import ChatMessage

PRIMARY_RESP = {
    "choices": [{"message": {"role": "assistant", "content": "primary ok"}}],
}
FALLBACK_RESP = {
    "choices": [{"message": {"role": "assistant", "content": "fallback ok"}}],
}


def _router(primary_url: str = "http://primary", fallback_url: str = "http://fallback", max_retries: int = 1) -> LLMRouter:
    return LLMRouter(
        primary={
            "base_url": primary_url,
            "api_key": "k1",
            "model": "m1",
            "timeout_sec": 5,
        },
        fallback={
            "base_url": fallback_url,
            "api_key": "k2",
            "model": "m2",
            "timeout_sec": 5,
        },
        max_retries=max_retries,
    )


def test_primary_success(respx_mock):
    respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(200, json=PRIMARY_RESP))
    router = _router(max_retries=1)
    reply = router.chat([ChatMessage(role="user", content="hi")])
    assert reply == "primary ok"


def test_fallback_after_primary_500(respx_mock):
    respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(500, json={"err": "boom"}))
    respx_mock.post("http://fallback/chat/completions").mock(return_value=httpx.Response(200, json=FALLBACK_RESP))
    router = _router(max_retries=1)
    reply = router.chat([ChatMessage(role="user", content="hi")])
    assert reply == "fallback ok"


def test_fallback_after_primary_retry_exhausted(respx_mock):
    # 第一次 500，第二次 502 → 重试耗尽 → 切 fallback
    primary_route = respx_mock.post("http://primary/chat/completions").mock(
        side_effect=[httpx.Response(500, json={"err": "1"}), httpx.Response(502, json={"err": "2"})]
    )
    respx_mock.post("http://fallback/chat/completions").mock(return_value=httpx.Response(200, json=FALLBACK_RESP))
    router = _router(max_retries=1)
    reply = router.chat([ChatMessage(role="user", content="hi")])
    assert reply == "fallback ok"
    assert primary_route.call_count == 2


def test_both_fail_raises_llm_call_error(respx_mock):
    respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(500, json={"err": "boom"}))
    respx_mock.post("http://fallback/chat/completions").mock(return_value=httpx.Response(502, json={"err": "down"}))
    router = _router(max_retries=0)
    with pytest.raises(LLMCallError) as exc:
        router.chat([ChatMessage(role="user", content="hi")])
    msg = str(exc.value).lower()
    assert "fallback failed" in msg


def test_fallback_after_primary_connect_timeout(respx_mock):
    def timeout(req):
        raise httpx.ConnectTimeout("timeout")

    respx_mock.post("http://primary/chat/completions").mock(side_effect=timeout)
    respx_mock.post("http://fallback/chat/completions").mock(return_value=httpx.Response(200, json=FALLBACK_RESP))
    router = _router(max_retries=0)
    reply = router.chat([ChatMessage(role="user", content="hi")])
    assert reply == "fallback ok"


def test_chat_converts_messages_to_openai_format(respx_mock):
    route = respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(200, json=PRIMARY_RESP))
    router = _router()
    router.chat([
        ChatMessage(role="system", content="you are helpful"),
        ChatMessage(role="user", content="hi"),
    ])
    request = route.calls.last.request
    import json
    body = json.loads(request.content)
    assert body["model"] == "m1"
    assert body["messages"] == [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
    ]
