# Phase 26 T1：LLMRouter.chat_with_tools

> 前置阅读：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（共享约定）
> 背景：现有 `LLMRouter.chat()` 只传 model+messages 返回 content 字符串，不支持 function calling。AgentLoop（T5）需要 OpenAI tools 协议：请求带 `tools`/`tool_choice`，响应取 `choices[0].message`（含 `tool_calls`）。

**Files:**
- Modify: `orchestrator/llm_router.py`
- Test: `tests/unit/test_llm_router_tools.py`（新建）

- [x] **Step 1: 写失败测试**

```python
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
```

- [x] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_llm_router_tools.py -v --basetemp=.pytest_basetemp`
Expected: 4 FAIL（`AttributeError: ... has no attribute 'chat_with_tools'`）

- [ ] **Step 3: 实现**

在 `orchestrator/llm_router.py` 的 `LLMRouter` 类内、`call()` 方法之前插入两个方法：

```python
    def _call_with_tools_once(self, provider: _Provider, messages: list[dict],
                              tools: list[dict], model: str | None = None) -> dict:
        """OpenAI tools 协议单次调用（Phase 26 T1）。"""
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or provider.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        with httpx.Client(timeout=provider.timeout_sec) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def chat_with_tools(self, messages: list[dict], tools: list[dict],
                        model: str | None = None) -> dict:
        """Phase 26 T1：function calling 主入口。

        messages 为 OpenAI dict 格式（含 role=tool 与 assistant.tool_calls
        轮次）；返回 choices[0].message（dict，content 已剥 <think>，
        tool_calls 原样保留）。主备 fallback 语义同 chat()。
        """
        last_err: Optional[Exception] = None
        for _ in range(self.max_retries + 1):
            try:
                data = self._call_with_tools_once(
                    self.primary, messages, tools, model)
                return self._extract_message(data)
            except (httpx.HTTPError, KeyError, IndexError) as e:
                last_err = e
        try:
            data = self._call_with_tools_once(
                self.fallback, messages, tools, model)
            return self._extract_message(data)
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise LLMCallError(
                f"primary failed ({last_err!r}), fallback failed ({e!r})"
            ) from e

    @staticmethod
    def _extract_message(data: dict) -> dict:
        """提取 choices[0].message；content 非 None 时剥 <think>。"""
        msg = dict(data["choices"][0]["message"])
        if msg.get("content"):
            msg["content"] = strip_think(msg["content"])
        return msg
```

注意：`_call_with_tools_once` 的 `model` 参数为**位置第 4 参**（测试 `m.call_args.args[4]` 断言依赖此顺序）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_llm_router_tools.py -v --basetemp=.pytest_basetemp`
Expected: 4 PASS

- [x] **Step 5: 既有 LLM 用例回归 + commit**（按用户指示本轮不 commit）

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_llm_router.py tests/unit/test_llm_router_tools.py -q --basetemp=.pytest_basetemp`（若 `test_llm_router.py` 不存在则只跑新文件）
Expected: 全 PASS

```bash
git add orchestrator/llm_router.py tests/unit/test_llm_router_tools.py
git commit -m "feat(phase26): LLMRouter.chat_with_tools（OpenAI tools 协议 + fallback + strip_think）"
```

完成后删除 `.pytest_basetemp` 目录，回总览勾选 T1。
