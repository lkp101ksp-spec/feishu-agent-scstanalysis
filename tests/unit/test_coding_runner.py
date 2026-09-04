"""CodingRunner：工具面拼装 + loop 驱动 + 结果回传（Phase 26 T7）。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.coding.coding_runner import (
    CodingRunner, _toolresult_to_dict,
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
