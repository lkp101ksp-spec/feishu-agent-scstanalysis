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
        # 第二轮收到 5 条：system+memory(成功段,Phase 29 T3)+user
        # +assistant(tool_calls)+tool
        msgs = llm.calls[1]["messages"]
        assert len(msgs) == 5
        assert "OK:" in msgs[1]["content"]             # 紧随 system 的记忆消息

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


class TestErrorSummary:
    """失败观察摘要进 tool_events（Phase 28 T1：诊断器可见真实报错）。"""

    def test_failed_event_carries_code_and_message(self):
        """skill 子进程失败（error_code+error_message）→ 事件 error 摘要含两者并截断。"""
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "总结", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {
            "ok": False, "error_code": "SCRIPT_ERROR",
            "error_message": "x" * 600}).run("sys", "任务")
        ev = r.tool_events[0]
        assert ev["ok"] is False
        assert ev["error"].startswith("SCRIPT_ERROR: ")
        assert len(ev["error"]) <= 500            # 500 字符截断

    def test_failed_event_error_from_exception(self):
        """dispatch 抛异常（error 字段）→ 事件 error 含异常类型。"""
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "总结", "tool_calls": None},
        ]
        llm = FakeLLM(seq)

        def boom(name, args):
            raise ValueError("bad input")

        r = _loop(llm, boom).run("sys", "任务")
        assert "ValueError: bad input" in r.tool_events[0]["error"]

    def test_success_event_has_no_error_key(self):
        """成功事件不追加 error 键（向后兼容，events 保持精简）。"""
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "done", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        r = _loop(llm, lambda n, a: {"ok": True, "value": 1}).run("sys", "任务")
        assert r.tool_events[0] == {"step": 1, "name": "t1", "ok": True,
                                    "args": "{}"}


class TestWorkingMemory:
    """滚动失败记忆（Phase 28 T2：模型每轮可见已试路径，减少重复试错）。"""

    def _fail_script(self, n):
        seq = [{"role": "assistant", "content": "",
                "tool_calls": [_call("t1", json.dumps({"p": i}), cid=f"c{i}")]}
               for i in range(n)]
        seq.append({"role": "assistant", "content": "总结", "tool_calls": None})
        return seq

    def test_memory_injected_after_failure(self):
        """首次失败 → 下一轮 messages 含"已试路径"消息（含工具与错误摘要）。"""
        llm = FakeLLM(self._fail_script(1))
        _loop(llm, lambda n, a: {"ok": False, "error": "SCRIPT_ERROR: boom"}
              ).run("sys", "任务")
        mem = [m for m in llm.calls[1]["messages"]
               if "已试路径" in str(m.get("content", ""))]
        assert len(mem) == 1
        assert "t1" in mem[0]["content"] and "boom" in mem[0]["content"]

    def test_memory_rolls_at_five_lines(self):
        """滚动窗口：失败超 5 行只留最近 5（直测 _refresh_memory_message，
        run() 全链因连败禁用 3 次即停，到不了 6+ 次失败）。"""
        from orchestrator.coding.agent_loop import AgentLoop
        lines = [f"- step {i}: t1({{}}) → err-{i}" for i in range(7)]
        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "任务"}]
        AgentLoop._refresh_memory_message(messages, lines, [])
        mem = next(m for m in messages
                   if str(m.get("content", "")).startswith("## 已试路径"))
        body = [ln for ln in mem["content"].splitlines() if ln.startswith("- ")]
        assert len(body) == 5
        assert "err-6" in mem["content"]          # 最新失败在内
        assert "err-1" not in mem["content"]      # 最旧失败被滚出
        # 原地替换：再次刷新不新增消息条数
        AgentLoop._refresh_memory_message(messages, lines, [])
        assert len([m for m in messages
                    if str(m.get("content", "")).startswith("## 已试路径")]) == 1

    def test_no_memory_without_failure(self):
        """无失败但有成功调用 → 注入成功段（Phase 29 T3）；无任何工具事件才不注入。"""
        seq = [
            {"role": "assistant", "content": "", "tool_calls": [_call("t1", cid="c1")]},
            {"role": "assistant", "content": "done", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        _loop(llm, lambda n, a: {"ok": True, "stdout": "merged.h5ad written"}
              ).run("sys", "任务")
        mem = [m for m in llm.calls[1]["messages"]
               if "已试路径" in str(m.get("content", ""))]
        assert len(mem) == 1
        assert "OK:" in mem[0]["content"]
        assert "merged.h5ad written" in mem[0]["content"]

    def test_no_memory_without_any_tool_call(self):
        """模型直接文字收尾（零工具调用）→ 不注入记忆消息。"""
        seq = [{"role": "assistant", "content": "答", "tool_calls": None}]
        llm = FakeLLM(seq)
        _loop(llm, lambda n, a: {"ok": True}).run("sys", "任务")
        assert not [m for m in llm.calls[0]["messages"]
                    if "已试路径" in str(m.get("content", ""))]


# === Phase 29 T3：成功路径摘要 ===


class TestWorkingMemorySuccess:
    """成功调用也进 Working Memory（可复用结果），失败段格式不变。"""

    def _ok_summary_line_shape(self):
        return None  # 形状断言见下述各用例

    def test_success_line_format(self):
        """成功行：- step N: name(args) → OK: <产出摘要>。"""
        from orchestrator.coding.agent_loop import AgentLoop
        messages = [{"role": "system", "content": "sys"}]
        AgentLoop._refresh_memory_message(
            messages, [], ["- step 1: merge({}) → OK: out.h5ad 90000 cells"])
        content = messages[1]["content"]
        assert content.startswith("## 已试路径")
        assert "### 成功（可复用结果）" in content
        assert "- step 1: merge({}) → OK: out.h5ad 90000 cells" in content
        assert "### 失败" not in content          # 无失败段

    def test_success_window_three_lines(self):
        """成功行窗口 3：超出只留最近 3。"""
        from orchestrator.coding.agent_loop import AgentLoop
        ok_lines = [f"- step {i}: t({{}}) → OK: r{i}" for i in range(6)]
        messages = [{"role": "system", "content": "sys"}]
        AgentLoop._refresh_memory_message(messages, [], ok_lines)
        body = [ln for ln in messages[1]["content"].splitlines()
                if ln.startswith("- ")]
        assert len(body) == 3
        assert "r5" in messages[1]["content"]     # 最近在内
        assert "r0" not in messages[1]["content"]  # 最旧滚出

    def test_fail_and_success_sections_coexist(self):
        """失败+成功混合：两段都在，失败行格式与 Phase 28 一致。"""
        from orchestrator.coding.agent_loop import AgentLoop
        messages = [{"role": "system", "content": "sys"}]
        AgentLoop._refresh_memory_message(
            messages,
            ["- step 2: t1({}) → SCRIPT_ERROR: boom"],
            ["- step 1: t2({}) → OK: fine"])
        content = messages[1]["content"]
        assert "### 失败（勿重复）" in content
        assert "### 成功（可复用结果）" in content
        assert "- step 2: t1({}) → SCRIPT_ERROR: boom" in content

    def test_ok_summary_from_observation(self):
        """_ok_summary：stdout 优先，result 次之，空则 'ok'。"""
        from orchestrator.coding.agent_loop import AgentLoop
        assert "merged ok" in AgentLoop._ok_summary({"stdout": "merged ok"})
        assert "42" in AgentLoop._ok_summary({"result": 42})
        assert AgentLoop._ok_summary({}) == "ok"

    def test_run_injects_success_memory(self):
        """全链：成功调用 → 下一轮消息含 OK 摘要行。"""
        seq = [
            {"role": "assistant", "content": "",
             "tool_calls": [_call("merge", cid="c1")]},
            {"role": "assistant", "content": "done", "tool_calls": None},
        ]
        llm = FakeLLM(seq)
        _loop(llm, lambda n, a: {"ok": True, "stdout": "93665 cells"}
              ).run("sys", "任务")
        mem = [m for m in llm.calls[1]["messages"]
               if "已试路径" in str(m.get("content", ""))]
        assert mem and "OK: 93665 cells" in mem[0]["content"]


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
        assert r.tool_events == [{"step": 1, "name": "t1", "ok": True,
                                  "args": "{}"}]
