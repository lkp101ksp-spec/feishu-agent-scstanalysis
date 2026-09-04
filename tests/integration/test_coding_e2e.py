"""/code 端到端冒烟：CodeTools + skill 工具 + registry 白名单真实协作（Phase 26 T8）。

不连外部服务：LLM 用脚本回放假件，skill 命令用本机 python -c。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.approval_broker import ApprovalBroker
from orchestrator.coding.coding_runner import CodingRunner
from orchestrator.tools.tool_handler import ToolHandler, ToolResult
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


class FakeLLM:
    """脚本回放假 LLM。"""

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

    def __init__(self, text, chat_id="chat_e2e", sender="ou_e2e", message_id="m"):
        self.text = text
        self.chat_id = chat_id
        self.sender_open_id = sender
        self.message_id = message_id


@pytest.fixture()
def e2e(tmp_path: Path):
    """全真件依赖（除 LLM/IM）：真 registry/handler/broker/workspace/skills。"""
    skills = tmp_path / "skills" / "reportgen"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: reportgen\ndescription: 生成统计报表 report\n---\n"
        "调用 run_report 生成统计报表。\n", encoding="utf-8")
    (skills / "tools.yaml").write_text(
        f"tools:\n"
        f"  - name: run_report\n"
        f"    description: 生成统计报表\n"
        f"    parameters: {{type: object, properties: {{tag: {{type: string}}}}}}\n"
        f"    command: [{Path(sys.executable).as_posix()}, -c, \"print('report generated')\"]\n",
        encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="sc_load", description="加载数据", risk_level="L2_side_effect",
        parameters={"type": "object", "properties": {}},
        handler=lambda **k: ToolResult(outputs={"cells": 1000}, artifacts_ids=[])))
    handler = ToolHandler(registry)
    im = MagicMock()
    broker = ApprovalBroker()

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

    runner = CodingRunner(llm=None, im=im, tool_handler=handler,
                          registry=registry, broker=broker, settings=_S())
    return {"runner": runner, "im": im, "broker": broker, "registry": registry,
            "tmp": tmp_path}


def test_e2e_code_primitives_and_final(e2e):
    """纯 code 原语链：write_file → run_cmd → final；无需审批。"""
    py = Path(sys.executable).as_posix()
    llm = FakeLLM([
        {"role": "assistant", "content": "",
         "tool_calls": [_call("write_file",
                              '{"path": "calc.py", "content": "print(6*7)"}', cid="c1")]},
        {"role": "assistant", "content": "",
         "tool_calls": [_call("run_cmd", json.dumps({"cmd": [py, "calc.py"]}), cid="c2")]},
        {"role": "assistant", "content": "计算结果 42，已写入 calc.py", "tool_calls": None},
    ])
    e2e["runner"].llm = llm
    r = e2e["runner"].run_sync(FakeIncoming("/code 算 6*7"), "算 6*7")
    assert r["status"] == "final"
    sid = e2e["runner"]._session_id("chat_e2e")
    assert (e2e["runner"].ws.session_dir(sid) / "calc.py").exists()
    texts = [str(c.args[1]) for c in e2e["im"].reply.call_args_list]
    assert any("42" in t or "计算结果" in t for t in texts)
    assert not e2e["im"].send_card.called          # 全程无审批卡


def test_e2e_skill_tool_with_approval(e2e):
    """skill 工具（L2）：发审批卡 → 线程侧 decide 放行 → 子进程真跑。"""
    import threading
    llm = FakeLLM([
        {"role": "assistant", "content": "",
         "tool_calls": [_call("run_report", '{"tag": "daily"}', cid="c1")]},
        {"role": "assistant", "content": "报表已生成", "tool_calls": None},
    ])
    e2e["runner"].llm = llm

    # 卡片发出后自动点批准（模拟用户）：轮询 send_card 被调即 decide
    def auto_approve():
        for _ in range(100):
            if e2e["im"].send_card.called:
                card = e2e["im"].send_card.call_args.args[1]
                actions = card["elements"][-1]["actions"]
                value = actions[0]["value"]
                e2e["broker"].decide(value["code_approval_id"], "approve",
                                     operator=value["owner"])
                return
            threading.Event().wait(0.02)

    approver = threading.Thread(target=auto_approve, daemon=True)
    approver.start()
    r = e2e["runner"].run_sync(FakeIncoming("/code 生成报表"), "生成报表")
    approver.join(timeout=5)
    assert r["status"] == "final"
    # 观察里含 skill 子进程 stdout
    tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "report generated" in tool_msg["content"]


def test_e2e_registry_whitelist_tool_dispatched(e2e):
    """registry 白名单 sc_*（L2）：审批放行后经 ToolHandler 执行返回 outputs。"""
    import threading
    llm = FakeLLM([
        {"role": "assistant", "content": "",
         "tool_calls": [_call("sc_load", "{}", cid="c1")]},
        {"role": "assistant", "content": "已加载 1000 细胞", "tool_calls": None},
    ])
    e2e["runner"].llm = llm

    # sc_load 为 L2_side_effect（spec §5：L2 工具单步审批卡），线程侧自动批准
    def auto_approve():
        for _ in range(100):
            if e2e["im"].send_card.called:
                card = e2e["im"].send_card.call_args.args[1]
                value = card["elements"][-1]["actions"][0]["value"]
                e2e["broker"].decide(value["code_approval_id"], "approve",
                                     operator=value["owner"])
                return
            threading.Event().wait(0.02)

    approver = threading.Thread(target=auto_approve, daemon=True)
    approver.start()
    r = e2e["runner"].run_sync(FakeIncoming("/code 加载数据"), "加载数据")
    approver.join(timeout=5)
    assert r["status"] == "final"
    tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "1000" in tool_msg["content"]


def test_e2e_clear_wipes_workspace(e2e):
    """/code clear 清空该会话工作区目录。"""
    runner = e2e["runner"]
    sid = runner._session_id("chat_e2e")
    (runner.ws.session_dir(sid) / "x.txt").write_text("x", encoding="utf-8")
    r = runner.handle(FakeIncoming("/code clear"))
    assert r["status"] == "coding_cleared"
    assert not (e2e["tmp"] / "code_ws" / sid).exists()
