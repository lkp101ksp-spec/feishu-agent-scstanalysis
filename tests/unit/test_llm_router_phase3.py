from unittest.mock import MagicMock

from orchestrator.llm_router import LLMRouter


def _make_router():
    fake = MagicMock()
    fake.post.return_value.json.return_value = {
        "choices": [{"message": {"content": "true"}}]
    }
    fake.post.return_value.raise_for_status.return_value = None
    primary = {"base_url": "http://x", "api_key": "k", "model": "m", "timeout_sec": 1}
    fallback = primary.copy()
    router = LLMRouter(primary=primary, fallback=fallback)
    # Patch _call_once to return fake data
    router._call_once = MagicMock(return_value={
        "choices": [{"message": {"content": "true"}}]
    })
    return router


def test_router_supports_call_role():
    router = _make_router()
    out = router.call(role="condition_eval", prompt="x")
    assert out == "true"


def test_router_call_role_loop_eval():
    router = _make_router()
    out = router.call(role="loop_eval", prompt="x")
    assert out == "true"


def test_router_call_role_context_compressor():
    router = _make_router()
    out = router.call(role="context_compressor", prompt="x")
    assert out == "true"