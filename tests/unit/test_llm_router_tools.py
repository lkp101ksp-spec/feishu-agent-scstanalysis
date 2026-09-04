"""LLMRouter.chat_with_tools：OpenAI function calling 协议（Phase 26 T1）。"""
from unittest.mock import patch

from orchestrator.llm_router import LLMRouter


def _router(max_retries: int = 1) -> LLMRouter:
    """构造测试路由（URL 假值，请求层全部 mock）。"""
    return LLMRouter(
        primary={"base_url": "http://p/v1", "api_key": "k", "model": "m1",
                 "timeout_sec": 5},
        fallback={"base_url": "http://f/v1", "api_key": "k", "model": "m2",
                  "timeout_sec": 5},
        max_retries=max_retries,
    )


def _tool_call_msg(name="read_file", args='{"path": "a.py"}', cid="call_1"):
    """构造 assistant 消息（含 tool_calls）。"""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function",
         "function": {"name": name, "arguments": args}}]}


def test_chat_with_tools_returns_tool_calls_message():
    """带 tool_calls 的响应原样返回，且 tools 透传到请求层。"""
    r = _router()
    resp = {"choices": [{"message": _tool_call_msg()}]}
    with patch.object(r, "_call_with_tools_once", return_value=resp) as m:
        msg = r.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}])
    assert msg["tool_calls"][0]["function"]["name"] == "read_file"
    assert m.call_args.args[2] == [{"type": "function",
                                    "function": {"name": "read_file"}}]


def test_chat_with_tools_strips_think_from_content():
    """content 中的 <think> 前缀被剥离；content 为 None 不炸。"""
    r = _router()
    resp = {"choices": [{"message": {
        "role": "assistant",
        "content": "<think>reasoning</think>done", "tool_calls": None}}]}
    with patch.object(r, "_call_with_tools_once", return_value=resp):
        msg = r.chat_with_tools([{"role": "user", "content": "hi"}], tools=[])
    assert msg["content"] == "done"


def test_chat_with_tools_primary_fail_falls_back():
    """主模型失败 → fallback 成功（max_retries=0：主 1 次 + 备 1 次）。"""
    r = _router(max_retries=0)
    fb = {"choices": [{"message": {"role": "assistant", "content": "ok",
                                   "tool_calls": None}}]}
    with patch.object(r, "_call_with_tools_once",
                      side_effect=[KeyError("boom"), fb]) as m:
        msg = r.chat_with_tools([{"role": "user", "content": "hi"}], tools=[])
    assert msg["content"] == "ok"
    assert m.call_count == 2
    assert m.call_args_list[1].args[0] is r.fallback


def test_chat_with_tools_model_override():
    """model 参数覆盖 provider 默认模型名（CODE_MODEL 配置入口）。"""
    r = _router()
    resp = {"choices": [{"message": {"role": "assistant", "content": "x",
                                     "tool_calls": None}}]}
    with patch.object(r, "_call_with_tools_once", return_value=resp) as m:
        r.chat_with_tools([{"role": "user", "content": "hi"}], tools=[],
                          model="kimi-coding")
    payload_model = m.call_args.kwargs.get("model")
    assert payload_model == "kimi-coding" or m.call_args.args[4] == "kimi-coding"
