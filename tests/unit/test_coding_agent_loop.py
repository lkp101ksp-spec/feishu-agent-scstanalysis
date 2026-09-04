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

    def test_tools_disabled_flag_set_after_three_failures(self):
        """连败禁用后文字收尾：status=final 且 tools_disabled=True（Phase 27 真机修复）。

        真机发现：连败禁用后模型按引导文字总结 → final，诊断链依赖该标记识别
        "实质失败的 final"。
        """
        seq = [{"role": "assistant", "content": "", "tool_calls": [_call("t1", cid=f"c{i}")]}
               for i in range(3)]
        seq.append({"role": "assistant", "content": "放弃工具，直接作答", "tool_calls": None})
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {"ok": False, "error": "x"}, max_steps=10).run("sys", "任务")
        assert r.status == "final"
        assert r.tools_disabled is True

    def test_tools_disabled_flag_false_on_normal_final(self):
        """正常 final（无连败禁用）→ tools_disabled=False。"""
        llm = FakeLLM([{"role": "assistant", "content": "答案", "tool_calls": None}])
        r = _loop(llm, lambda n, a: {"ok": True}).run("sys", "任务")
        assert r.status == "final" and r.tools_disabled is False


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
