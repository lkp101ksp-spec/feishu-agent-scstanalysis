# Phase 26 T5：AgentLoop 状态机

> 前置阅读：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（共享约定）
> 前置任务：T1（`LLMRouter.chat_with_tools` 已就绪）
> 背景：spec §4——think→act→observe 循环，三重终止（步数 25 / token 预算 200k / 超时 3600s）+ 连续失败禁用 + 单条观察截断 + 预算临界压缩。L2 工具经 risk_map 查得后走 `approve_fn`（`(info: list[str]) -> bool`），拒绝即回 `APPROVAL_DENIED` 观察，不中断循环。

**Files:**
- Create: `orchestrator/coding/agent_loop.py`
- Test: `tests/unit/test_coding_agent_loop.py`

**接口契约**（与总览一致）：
- `AgentLoop(llm, tools_schema: list[dict], dispatch: Callable[[str, object], dict], *, max_steps=25, token_budget=200000, timeout_sec=3600, risk_map: dict[str, str] | None = None, approve_fn=None, on_step: Callable[[int, dict], None] | None = None, model: str | None = None)`
- `.run(system: str, task: str) -> LoopResult`
- `LoopResult(status, final_text, steps, approx_tokens, abort_reason="", tool_events=None)`——status ∈ `final / max_steps / budget_exhausted / timeout / no_tools`
- `llm` 只需鸭子类型满足 `chat_with_tools(messages, tools, model=None) -> dict`（返回 OpenAI message dict：`content` + `tool_calls: [{id, function: {name, arguments}}]`）——测试用 FakeLLM，线上传 LLMRouter
- `dispatch(name, arguments)` 为 T3 的 `CodeTools.dispatch` 或 T7 里拼装的统一分发函数（异常安全，返回 dict）
- risk_map 缺省时未知工具按 `"L1"` 处理（不审批直接执行）；`"L2"` 工具执行前必须 `approve_fn` 放行

- [ ] **Step 1: 写失败测试**

```python
"""AgentLoop：三终止 + 截断压缩 + 失败禁用 + L2 审批（Phase 26 T5）。"""
import json

from orchestrator.coding.agent_loop import AgentLoop


def _call(name: str, args: str = "{}", cid: str = "c1") -> dict:
    """构造一条 OpenAI 格式 tool_call。"""
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


class FakeLLM:
    """按脚本逐条回放的假 LLM；深拷贝记录每次收到的 messages/tools/model。"""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat_with_tools(self, messages, tools, model=None):
        self.calls.append({
            "messages": json.loads(json.dumps(messages)),
            "tools": list(tools), "model": model,
        })
        return self.responses.pop(0)


SCHEMA = [{"type": "function", "function": {"name": "t1",
           "parameters": {"type": "object", "properties": {}}}}]


def _loop(llm, dispatch, **kw) -> AgentLoop:
    """构造默认参数 AgentLoop。"""
    return AgentLoop(llm, SCHEMA, dispatch, **kw)


class TestTerminations:
    def test_final_immediately(self):
        llm = FakeLLM([{"role": "assistant", "content": "答案在此", "tool_calls": None}])
        r = _loop(llm, lambda n, a: {"ok": True}).run("sys", "任务")
        assert r.status == "final" and r.final_text == "答案在此" and r.steps == 1

    def test_two_tool_steps_then_final(self):
        llm = FakeLLM([
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "完成", "tool_calls": None},
        ])
        seen: list[tuple[str, dict]] = []

        def dispatch(name, args):
            seen.append((name, args))
            return {"ok": True, "value": 42}

        r = _loop(llm, dispatch).run("sys", "任务")
        assert r.status == "final" and r.steps == 2
        assert seen == [("t1", {})]                    # dict 参数原样传递
        # 第二轮收到 4 条：system+user+assistant(tool_calls)+tool
        assert len(llm.calls[1]["messages"]) == 4

    def test_max_steps_exhausted(self):
        resp = {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="cx")]}
        llm = FakeLLM([dict(resp)] * 10)
        r = _loop(llm, lambda n, a: {"ok": True}, max_steps=5).run("sys", "任务")
        assert r.status == "max_steps" and r.steps == 5

    def test_timeout(self):
        llm = FakeLLM([])
        r = _loop(llm, lambda n, a: {"ok": True}, timeout_sec=0).run("sys", "任务")
        assert r.status == "timeout" and llm.calls == []  # 未发起任何 LLM 调用

    def test_budget_exhausted_after_compress(self):
        """预算极小 → 压缩一次仍超 → 优雅收尾。"""
        big = {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]}
        llm = FakeLLM([dict(big)] * 30)
        r = _loop(llm, lambda n, a: {"ok": True, "blob": "x" * 5000}, max_steps=30,
                  token_budget=200).run("sys", "任务")
        assert r.status == "budget_exhausted"


class TestFailureDisable:
    def test_three_consecutive_failures_disable_tools(self):
        """连续 3 次失败 → 第 4 轮起 tools 为空表；给纯文本即 final。"""
        seq = [{"role": "assistant", "content": "", "tool_calls": [_call("t1", cid=f"c{i}")]}
               for i in range(3)]
        seq.append({"role": "assistant", "content": "放弃工具，直接作答", "tool_calls": None})
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {"ok": False, "error": "boom"}, max_steps=10).run("sys", "任务")
        assert r.status == "final"
        assert llm.calls[3]["tools"] == []               # 第 4 轮工具被禁用
        # 禁用告知消息已注入第 4 轮输入
        texts = [m.get("content", "") for m in llm.calls[3]["messages"] if m["role"] == "user"]
        assert any("禁用" in t for t in texts)

    def test_success_resets_failure_counter(self):
        """失败-成功交替不禁用（计数器被成功重置）。"""
        seq = [{"role": "assistant", "content": "", "tool_calls": [_call("t1", cid=f"c{i}")]}
               for i in range(4)]
        seq.append({"role": "assistant", "content": "done", "tool_calls": None})
        llm = FakeLLM(seq)
        results = iter([{"ok": False, "error": "x"}, {"ok": True},
                        {"ok": False, "error": "x"}, {"ok": True}])
        r = _loop(llm, lambda n, a: next(results), max_steps=10).run("sys", "任务")
        assert r.status == "final" and llm.calls[4]["tools"] != []

    def test_disabled_then_toolcall_again_aborts(self):
        """禁用后模型仍想调工具 → no_tools 终止。"""
        seq = [{"role": "assistant", "content": "", "tool_calls": [_call("t1", cid=f"c{i}")]}
               for i in range(4)]
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {"ok": False, "error": "x"}, max_steps=10).run("sys", "任务")
        assert r.status == "no_tools"


class TestApproval:
    def test_l2_denied_returns_observation_without_dispatch(self):
        calls: list[str] = []
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "被拒了", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: calls.append(n) or {"ok": True},
                  risk_map={"t1": "L2"}, approve_fn=lambda info: False).run("sys", "任务")
        assert r.status == "final"
        assert calls == []                               # dispatch 未被调用
        # 第二轮输入的最后一条是 tool 消息，内容为 APPROVAL_DENIED
        last = llm.calls[1]["messages"][-1]
        assert last["role"] == "tool" and "APPROVAL_DENIED" in last["content"]

    def test_l2_approved_dispatches(self):
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "ok", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        seen: list[str] = []
        r = _loop(llm, lambda n, a: seen.append(n) or {"ok": True},
                  risk_map={"t1": "L2"}, approve_fn=lambda info: True).run("sys", "任务")
        assert r.status == "final" and seen == ["t1"]


class TestDetails:
    def test_tool_result_truncated(self):
        """超长观察截断回填（含 truncated 标记，长度受控）。"""
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "done", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {"ok": True, "blob": "y" * 50000}).run("sys", "任务")
        assert r.status == "final"
        last = llm.calls[1]["messages"][-1]
        assert last["role"] == "tool"
        assert "truncated" in last["content"] and len(last["content"]) < 5000

    def test_on_step_callback_invoked(self):
        events: list[dict] = []
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "fin", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        r = AgentLoop(llm, SCHEMA, lambda n, a: {"ok": True},
                      on_step=lambda i, ev: events.append(ev)).run("sys", "任务")
        assert r.status == "final"
        kinds = [e["event"] for e in events]
        assert "tool" in kinds and "final" in kinds

    def test_model_forwarded(self):
        llm = FakeLLM([{"role": "assistant", "content": "x", "tool_calls": None}])
        _loop(llm, lambda n, a: {"ok": True}, model="deepseek-v3").run("sys", "t")
        assert llm.calls[0]["model"] == "deepseek-v3"

    def test_tool_events_collected(self):
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "fin", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {"ok": True, "n": 1}).run("sys", "任务")
        assert r.tool_events == [{"step": 1, "name": "t1", "ok": True}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_agent_loop.py -v --basetemp=.pytest_basetemp`
Expected: collection FAIL（`ModuleNotFoundError: orchestrator.coding.agent_loop`）

- [ ] **Step 3: 实现**

创建 `orchestrator/coding/agent_loop.py`：

```python
"""自研 agentic loop：think→act→observe 多轮函数调用循环（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §4：
- 三重终止：max_steps(25) / token_budget(200k) / timeout_sec(3600)
- 连续 3 次工具失败 → 禁用工具再问一轮（逼模型纯文本收尾，仍调工具则 no_tools）
- 单条观察 >4k 字符截断；预算临界触发一次 compress_messages 历史压缩
- risk_map 标记的 L2 工具执行前必须 approve_fn 放行
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

OBS_TRUNCATE = 4000          # 单条观察字符上限
FAIL_DISABLE = 3             # 连续失败阈值
COMPRESS_KEEP_TAIL = 8       # 压缩时保留的最近消息条数


@dataclass
class LoopResult:
    """一次 loop 运行的结果。"""
    status: str                       # final/max_steps/budget_exhausted/timeout/no_tools
    final_text: str
    steps: int                        # 已消耗的 LLM 轮数
    approx_tokens: int
    abort_reason: str = ""
    tool_events: list[dict] = field(default_factory=list)  # {step,name,ok}


def truncate_observation(payload: dict, limit: int = OBS_TRUNCATE) -> str:
    """观察 dict → JSON 字符串，超限头尾保留截断。"""
    s = json.dumps(payload, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s
    head, tail = limit * 45 // 100, limit * 30 // 100
    return s[:head] + f"\n...[truncated {len(s) - head - tail} chars]...\n" + s[-tail:]


def compress_messages(messages: list[dict], keep_tail: int = COMPRESS_KEEP_TAIL) -> list[dict]:
    """历史压缩：system + 中段一行简报 + 最近 keep_tail 条。"""
    if len(messages) <= keep_tail + 1:
        return messages
    head = messages[0]
    mid = messages[1:-keep_tail]
    tail = messages[-keep_tail:]
    n_tools = sum(1 for m in mid if m.get("role") == "tool")
    digest = {"role": "user", "content":
              f"[上下文压缩] 此前 {len(mid)} 条消息（含 {n_tools} 次工具观察）已折叠。"}
    return [head, digest, *tail]


class AgentLoop:
    """多轮 function-calling 循环；llm 只需实现 chat_with_tools。"""

    def __init__(
        self,
        llm,
        tools_schema: list[dict],
        dispatch: Callable[[str, object], dict],
        *,
        max_steps: int = 25,
        token_budget: int = 200_000,
        timeout_sec: int = 3600,
        risk_map: Optional[dict[str, str]] = None,
        approve_fn: Optional[Callable[[list[str]], bool]] = None,
        on_step: Optional[Callable[[int, dict], None]] = None,
        model: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.tools_schema = tools_schema
        self.dispatch = dispatch
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.timeout_sec = timeout_sec
        self.risk_map = risk_map or {}
        self.approve_fn = approve_fn
        self.on_step = on_step
        self.model = model
        self.events: list[dict] = []   # tool_event 累积（随 LoopResult 返回）

    # ------------------------------------------------------------------ #
    def run(self, system: str, task: str) -> LoopResult:
        """执行循环直至终止；返回 LoopResult。"""
        started = time.monotonic()
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        failures = 0
        disabled = False
        compressed = False

        for step in range(1, self.max_steps + 1):
            if time.monotonic() - started > self.timeout_sec:
                return LoopResult("timeout", "", step - 1,
                                  self._approx_tokens(messages), "timeout exceeded",
                                  self.events)

            resp = self.llm.chat_with_tools(
                messages, [] if disabled else self.tools_schema, model=self.model)
            content = (resp.get("content") or "").strip()
            calls = resp.get("tool_calls") or []

            if disabled and calls:
                return LoopResult("no_tools", content, step,
                                  self._approx_tokens(messages),
                                  "model kept requesting tools after disable",
                                  self.events)

            if not calls:
                self._emit(step, {"event": "final", "text": content[:200]})
                return LoopResult("final", content, step,
                                  self._approx_tokens(messages), "", self.events)

            messages.append({"role": "assistant", "content": resp.get("content") or "",
                             "tool_calls": calls})
            step_all_ok = True
            for tc in calls:
                name = tc["function"]["name"]
                arguments = tc["function"].get("arguments", "{}")
                obs = self._execute_tool(name, arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": truncate_observation(obs),
                })
                ok = bool(obs.get("ok"))
                self.events.append({"step": step, "name": name, "ok": ok})
                self._emit(step, {"event": "tool", "name": name, "ok": ok})
                if not ok:
                    step_all_ok = False

            failures = 0 if step_all_ok else failures + 1
            if failures >= FAIL_DISABLE and not disabled:
                disabled = True
                messages.append({"role": "user", "content":
                                 "工具已连续多次失败并被临时禁用，请直接用文字总结结论。"})
                self._emit(step, {"event": "tools_disabled"})

            # 预算：超限先压缩一次，仍超则优雅收尾
            if self._approx_tokens(messages) > self.token_budget:
                if not compressed and len(messages) > COMPRESS_KEEP_TAIL + 1:
                    messages = compress_messages(messages)
                    compressed = True
                    self._emit(step, {"event": "compressed", "n_msgs": len(messages)})
                if self._approx_tokens(messages) > self.token_budget:
                    return LoopResult("budget_exhausted", content or "", step,
                                      self._approx_tokens(messages),
                                      "token budget exceeded after compress",
                                      self.events)

        return LoopResult("max_steps", "", self.max_steps,
                          self._approx_tokens(messages), "max steps reached",
                          self.events)

    # ------------------------------------------------------------------ #
    def _execute_tool(self, name: str, arguments) -> dict:
        """L2 审批 → dispatch；异常与审批拒绝均转观察 dict。"""
        if self.risk_map.get(name, "L1").startswith("L2"):
            info = [f"skill_tool: {name}", str(arguments)[:200]]
            if self.approve_fn is None or not self.approve_fn(info):
                logger.info("L2 tool denied by user: %s", name)
                return {"ok": False, "error": "APPROVAL_DENIED", "tool": name}
        try:
            result = self.dispatch(name, arguments)
        except Exception as exc:  # noqa: BLE001 —— 观察必须回传而非崩溃
            logger.warning("dispatch %s raised: %s", name, exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return result if isinstance(result, dict) else {"ok": True, "result": result}

    def _approx_tokens(self, messages: list[dict]) -> int:
        """粗估 token：消息 JSON 字符数 / 4。"""
        return sum(len(json.dumps(m, ensure_ascii=False, default=str))
                   for m in messages) // 4

    def _emit(self, step: int, event: dict) -> None:
        """触发 on_step 回调（CodingRunner 用于过程反馈节流）。"""
        if self.on_step is not None:
            try:
                self.on_step(step, event)
            except Exception:  # noqa: BLE001 —— 回调绝不影响主流程
                logger.exception("on_step callback failed")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_agent_loop.py -v --basetemp=.pytest_basetemp`
Expected: 全 PASS（13 用例）

- [ ] **Step 5: commit**

```bash
git add orchestrator/coding/agent_loop.py tests/unit/test_coding_agent_loop.py
git commit -m "feat(phase26): AgentLoop 状态机（三终止/截断压缩/失败禁用/L2 审批）"
```

完成后删除 `.pytest_basetemp`，回总览勾选 T5。
