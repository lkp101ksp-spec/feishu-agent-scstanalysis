"""CodingRunner：工具面拼装 + loop 驱动 + 结果回传（Phase 26 T7）。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.coding.coding_runner import (
    CodingRunner,
    _toolresult_to_dict,
)
from orchestrator.tools.tool_handler import ToolHandler, ToolResult
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


class FakeLLM:
    """脚本回放假 LLM（chat_with_tools）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_with_tools(self, messages, tools, model=None):
        self.calls.append({"messages": json.loads(json.dumps(messages)),
                           "tools": json.loads(json.dumps(tools)), "model": model})
        return self.responses.pop(0)


def _call(name, args="{}", cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


class FakeIncoming:
    """最小 IncomingMessage 替身。"""

    def __init__(self, text, chat_id="c1", sender="ou_1", message_id="m1"):
        self.text = text
        self.chat_id = chat_id
        self.sender_open_id = sender
        self.message_id = message_id


@pytest.fixture()
def deps(tmp_path: Path):
    """构造 CodingRunner 全套假依赖（settings 指向 tmp，防污染仓库目录）。"""
    registry = ToolRegistry()
    handler = ToolHandler(registry)
    im = MagicMock()
    broker = MagicMock()

    class _S:
        """最小 settings 替身（字段名与 Settings 一致）。"""
        code_workspace_root = str(tmp_path / "code_ws")
        code_skills_dir = str(tmp_path / "skills")
        code_max_steps = 10
        code_token_budget = 200000
        code_timeout_sec = 120
        code_model = ""
        code_registry_tools = "sc_*"
        research_approval_timeout_sec = 30

    return {"registry": registry, "handler": handler, "im": im, "broker": broker,
            "tmp": tmp_path, "settings": _S}


def _runner(llm, deps, settings=None) -> CodingRunner:
    return CodingRunner(llm=llm, im=deps["im"], tool_handler=deps["handler"],
                        registry=deps["registry"], broker=deps["broker"],
                        settings=settings or deps["settings"])


class TestHandle:
    def test_usage_without_task(self, deps):
        runner = _runner(FakeLLM([]), deps)
        r = runner.handle(FakeIncoming("/code"))
        assert r["status"] == "coding_usage"
        deps["im"].reply.assert_called_once()

    def test_clear_removes_workspace(self, deps):
        runner = _runner(FakeLLM([]), deps)
        sid = runner._session_id("c1")
        (runner.ws.session_dir(sid) / "junk.txt").write_text("x", encoding="utf-8")
        r = runner.handle(FakeIncoming("/code clear"))
        assert r["status"] == "coding_cleared"
        assert not (Path(runner.ws.root) / sid).exists()

    def test_accepted_returns_immediately(self, deps):
        runner = _runner(FakeLLM([]), deps)
        runner.run_sync = MagicMock(return_value={"status": "done"})
        r = runner.handle(FakeIncoming("/code 做点事"))
        assert r["status"] == "coding_accepted"


class TestRunSync:
    def test_write_and_run_full_flow(self, deps):
        """write_file → run_cmd(python) → final：文件落盘 + 结果回复。"""
        py = Path(sys.executable).as_posix()
        llm = FakeLLM([
            {"role": "assistant", "content": "",
             "tool_calls": [_call("write_file",
                                  '{"path": "hello.py", "content": "print(\'hi\')"}', cid="c1")]},
            {"role": "assistant", "content": "",
             "tool_calls": [_call("run_cmd", json.dumps({"cmd": [py, "hello.py"]}), cid="c2")]},
            {"role": "assistant", "content": "已创建并运行，输出 hi", "tool_calls": None},
        ])
        runner = _runner(llm, deps)
        r = runner.run_sync(FakeIncoming("/code 写并运行"), "写并运行")
        assert r["status"] == "final"
        sid = runner._session_id("c1")
        assert (runner.ws.session_dir(sid) / "hello.py").read_text(encoding="utf-8") == "print('hi')"
        # 工具观察进入第二轮消息（run_cmd 的 stdout 含 hi）
        tool_msgs = [m for m in llm.calls[2]["messages"] if m["role"] == "tool"]
        assert "hi" in tool_msgs[-1]["content"]
        # 结果回复至少一次，含 final 文本
        texts = [str(c.args[1]) for c in deps["im"].reply.call_args_list]
        assert any("已创建并运行" in t for t in texts)

    def test_system_prompt_contains_skill_knowledge(self, deps):
        """SKILL.md 知识面按词元命中拼进 system。"""
        skill = deps["tmp"] / "skills" / "bioqc"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: bioqc\ndescription: 单细胞质控 qc\n---\n对 h5ad 做质控。\n",
            encoding="utf-8")
        llm = FakeLLM([{"role": "assistant", "content": "done", "tool_calls": None}])
        runner = _runner(llm, deps)
        runner.skills_dir = deps["tmp"] / "skills"
        runner.run_sync(FakeIncoming("/code 做单细胞质控"), "做单细胞质控")
        system = llm.calls[0]["messages"][0]["content"]
        assert "bioqc" in system

    def test_registry_whitelist_tool_in_schema(self, deps):
        """sc_* 前缀 registry 工具进入 schema，其他不进。"""
        deps["registry"].register(ToolSpec(
            name="sc_load", description="加载", risk_level="L2_side_effect",
            parameters={"type": "object", "properties": {}}, handler=lambda **k: None))
        deps["registry"].register(ToolSpec(
            name="blast_search", description="BLAST", risk_level="L0_read",
            parameters={"type": "object", "properties": {}}, handler=lambda **k: None))
        llm = FakeLLM([{"role": "assistant", "content": "ok", "tool_calls": None}])
        runner = _runner(llm, deps)
        runner.run_sync(FakeIncoming("/code t"), "t")
        names = {t["function"]["name"] for t in llm.calls[0]["tools"]}
        assert "sc_load" in names and "write_file" in names
        assert "blast_search" not in names

    def test_l2_skill_tool_requires_approval(self, deps):
        """skill 工具（L2）审批拒绝时不执行 handler。"""
        skill = deps["tmp"] / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: 演示 skill\n---\n正文\n", encoding="utf-8")
        (skill / "tools.yaml").write_text(
            f"tools:\n  - name: run_demo\n    description: 演示工具\n"
            f"    parameters: {{type: object, properties: {{}}}}\n"
            f"    command: [{Path(sys.executable).as_posix()}, -c, print('demo')]\n",
            encoding="utf-8")
        deps["broker"].wait.return_value = "deny"     # 用户拒绝
        llm = FakeLLM([
            {"role": "assistant", "content": "",
             "tool_calls": [_call("run_demo", "{}", cid="c1")]},
            {"role": "assistant", "content": "被拒，改为说明", "tool_calls": None},
        ])
        runner = _runner(llm, deps)
        runner.skills_dir = deps["tmp"] / "skills"
        r = runner.run_sync(FakeIncoming("/code t"), "t")
        assert r["status"] == "final"
        # 审批卡已发，且观察为 APPROVAL_DENIED
        assert deps["im"].send_card.called
        tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][-1]
        assert "APPROVAL_DENIED" in tool_msg["content"]

    def test_exception_in_thread_body_replies_error(self, deps):
        """线程主体异常：回复错误而非静默。"""
        llm = FakeLLM([])
        llm.chat_with_tools = MagicMock(side_effect=RuntimeError("llm down"))
        runner = _runner(llm, deps)
        r = runner.run_sync(FakeIncoming("/code t"), "t")
        assert r["status"] == "error"
        texts = [str(c.args[1]) for c in deps["im"].reply.call_args_list]
        assert any("异常" in t for t in texts)


class TestHelpers:
    def test_toolresult_to_dict_ok(self):
        tr = ToolResult(outputs={"stdout": "x"}, artifacts_ids=[])
        d = _toolresult_to_dict(tr)
        assert d == {"ok": True, "stdout": "x"}

    def test_toolresult_to_dict_error(self):
        tr = ToolResult(outputs={}, artifacts_ids=[], error_code="SCRIPT_ERROR",
                        error_message="boom")
        d = _toolresult_to_dict(tr)
        assert d["ok"] is False and "SCRIPT_ERROR" in d["error"]

    def test_session_id_stable_per_chat(self, deps):
        runner = _runner(FakeLLM([]), deps)
        assert runner._session_id("c1") == runner._session_id("c1")
        assert runner._session_id("c1") != runner._session_id("c2")


# === Phase 39：进度卡 v2（_ProgressCard + _make_reporter 回退） ===

from types import SimpleNamespace  # noqa: E402

from orchestrator.coding.coding_runner import (  # noqa: E402
    _ProgressCard,
    _ProgressReporter,
)


def _result(status="final", steps=5, final_text="任务搞定"):
    """最小 LoopResult 替身。"""
    return SimpleNamespace(status=status, steps=steps, final_text=final_text,
                           tools_disabled=False)


@pytest.fixture()
def card():
    """_ProgressCard：mock im 发卡成功（om_card）+ 可控时钟。"""
    im = MagicMock()
    im.send_card.return_value = "om_card"
    clock = {"t": 100.0}
    pc = _ProgressCard(im, "oc_1", "写一个统计脚本", every=3,
                       min_interval_sec=2.0, now=lambda: clock["t"])
    pc._clock = clock  # 测试内拨时间
    return pc, im, clock


class TestProgressCard:
    def test_start_sends_initial_card(self, card):
        """受理发卡：拿到 message_id → 走卡片路径；卡面含任务预览。"""
        pc, im, clock = card
        assert pc.start() is True
        im.send_card.assert_called_once()
        sent = im.send_card.call_args.args[1]
        assert sent["header"] == "代码任务进行中 · step 0"
        assert "写一个统计脚本" in sent["elements"][0]["text"]["content"]

    def test_start_send_failure_returns_false(self, card):
        """发卡异常 → False（调用方回退 v1 文本节流）。"""
        pc, im, clock = card
        im.send_card.side_effect = RuntimeError("im down")
        assert pc.start() is False

    def test_start_no_message_id_returns_false(self, card):
        """发卡未返回 message_id（CLI 老路径）→ False 回退 v1。"""
        pc, im, clock = card
        im.send_card.return_value = ""
        assert pc.start() is False

    def test_tool_events_throttled_every_n(self, card):
        """每 3 个工具事件刷新一次；不足 N 不刷新。"""
        pc, im, clock = card
        pc.start()
        pc(1, {"event": "tool", "name": "write_file", "ok": True})
        pc(1, {"event": "tool", "name": "read_file", "ok": True})
        assert im.update_card.call_count == 0
        clock["t"] += 3
        pc(1, {"event": "tool", "name": "run_cmd", "ok": True})
        assert im.update_card.call_count == 1
        updated = im.update_card.call_args.args[1]
        assert "step 1" in updated["header"]
        body = updated["elements"][0]["text"]["content"]
        assert "✓ write_file" in body and "✗" not in body

    def test_min_interval_guard(self, card):
        """频率护栏：距上次刷新不足 min_interval 不刷新（PATCH 限频）。"""
        pc, im, clock = card
        pc.start()
        clock["t"] += 3
        for _ in range(3):
            pc(1, {"event": "tool", "name": "t1", "ok": True})
        assert im.update_card.call_count == 1
        # 立刻再来 3 个（间隔 < 2s）：不刷
        for _ in range(3):
            pc(2, {"event": "tool", "name": "t2", "ok": True})
        assert im.update_card.call_count == 1
        # 拨快时钟过护栏：再来 3 个（第 9 个事件整除）刷第二次
        clock["t"] += 3
        for _ in range(3):
            pc(2, {"event": "tool", "name": "t3", "ok": True})
        assert im.update_card.call_count == 2

    def test_key_events_update_immediately(self, card):
        """关键事件（tools_disabled/compressed）即时刷新且进事件流。"""
        pc, im, clock = card
        pc.start()
        clock["t"] += 3
        pc(2, {"event": "tools_disabled"})
        assert im.update_card.call_count == 1
        body = im.update_card.call_args.args[1]["elements"][0]["text"]["content"]
        assert "工具连续失败已临时禁用" in body

    def test_events_history_capped(self, card):
        """卡面事件流只保留最近 5 条。"""
        pc, im, clock = card
        pc.start()
        for i in range(8):
            pc._events.append(f"✓ t{i}")
        assert len(pc._events) == 5
        assert pc._events[0] == "✓ t3"

    def test_update_failure_breaks_and_stops(self, card):
        """刷新失败 → broken 停更（卡面停最后状态），后续事件/终态不再调。"""
        pc, im, clock = card
        pc.start()
        clock["t"] += 3
        im.update_card.side_effect = RuntimeError("patch down")
        pc(1, {"event": "tool", "name": "x", "ok": True})
        pc(1, {"event": "tool", "name": "y", "ok": True})
        pc(1, {"event": "tool", "name": "z", "ok": True})
        assert im.update_card.call_count == 1  # 首次失败后熔断
        clock["t"] += 10
        pc(2, {"event": "tools_disabled"})
        pc.finish(_result())
        assert im.update_card.call_count == 1

    def test_finish_renders_final_state(self, card):
        """终态定格：完成头 + steps + 耗时 + 最终答复预览。"""
        pc, im, clock = card
        pc.start()
        clock["t"] += 65
        pc.finish(_result())
        final_card = im.update_card.call_args.args[1]
        assert final_card["header"] == "代码任务已完成"
        body = final_card["elements"][0]["text"]["content"]
        assert "5 steps" in body and "65s" in body and "任务搞定" in body

    def test_finish_error_renders_error(self, card):
        pc, im, clock = card
        pc.start()
        pc.finish_error()
        err_card = im.update_card.call_args.args[1]
        assert err_card["header"] == "代码任务异常终止"
        assert "❌" in err_card["elements"][0]["text"]["content"]

    def test_call_without_start_is_noop(self, card):
        """未 start（无 message_id）时事件/终态全部静默。"""
        pc, im, clock = card
        pc(1, {"event": "tool", "name": "x", "ok": True})
        pc.finish(_result())
        im.update_card.assert_not_called()


class TestMakeReporter:
    def test_card_path_when_message_id_returned(self, deps):
        """发卡拿到 message_id → v2 进度卡。"""
        deps["im"].send_card.return_value = "om_card"
        runner = _runner(FakeLLM([]), deps)
        rep = runner._make_reporter(FakeIncoming("/code t"), "t")
        assert isinstance(rep, _ProgressCard)

    def test_fallback_v1_when_no_message_id(self, deps):
        """发卡无 message_id → v1 文本节流。"""
        deps["im"].send_card.return_value = ""
        runner = _runner(FakeLLM([]), deps)
        rep = runner._make_reporter(FakeIncoming("/code t"), "t")
        assert isinstance(rep, _ProgressReporter)

    def test_fallback_v1_when_send_raises(self, deps):
        deps["im"].send_card.side_effect = RuntimeError("im down")
        runner = _runner(FakeLLM([]), deps)
        rep = runner._make_reporter(FakeIncoming("/code t"), "t")
        assert isinstance(rep, _ProgressReporter)

    def test_run_sync_final_card_updated(self, deps):
        """全链集成：run_sync 完成后进度卡定格为「已完成」终态。"""
        llm = FakeLLM([
            {"role": "assistant", "content": "直接答复", "tool_calls": None},
        ])
        deps["im"].send_card.return_value = "om_card"
        runner = _runner(llm, deps)
        r = runner.run_sync(FakeIncoming("/code t"), "t")
        assert r["status"] == "final"
        final_card = deps["im"].update_card.call_args.args[1]
        assert final_card["header"] == "代码任务已完成"
        assert "直接答复" in final_card["elements"][0]["text"]["content"]

    def test_run_sync_crash_sets_error_card(self, deps):
        """loop 崩溃：进度卡定格「异常终止」，错误文本仍兜底回复。"""
        llm = FakeLLM([])
        llm.chat_with_tools = MagicMock(side_effect=RuntimeError("llm down"))
        deps["im"].send_card.return_value = "om_card"
        runner = _runner(llm, deps)
        r = runner.run_sync(FakeIncoming("/code t"), "t")
        assert r["status"] == "error"
        err_card = deps["im"].update_card.call_args.args[1]
        assert err_card["header"] == "代码任务异常终止"
        texts = [str(c.args[1]) for c in deps["im"].reply.call_args_list]
        assert any("异常" in t for t in texts)
        assert runner._session_id("c1") != runner._session_id("c2")
