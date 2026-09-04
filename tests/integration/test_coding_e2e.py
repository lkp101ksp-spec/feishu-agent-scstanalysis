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
    """脚本回放假 LLM（chat_with_tools 驱动 loop；chat 供 SkillDiagnoser 诊断）。"""

    def __init__(self, responses, chat_response=""):
        self.responses = list(responses)
        self.calls = []
        self.chat_response = chat_response

    def chat_with_tools(self, messages, tools, model=None):
        self.calls.append({"messages": json.loads(json.dumps(messages)),
                           "tools": json.loads(json.dumps(tools)), "model": model})
        return self.responses.pop(0)

    def chat(self, messages, model=None):
        """SkillDiagnoser 诊断调用：返回固定 JSON 字符串。"""
        return self.chat_response


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


# === Phase 27：skill 失败诊断 → 改进审批卡 → 批准写回 ===
# 失败轨迹构造：LLM 连续以非法 JSON 参数调 skill 工具（BAD_ARGUMENTS_JSON，
# 真实 ok=False 事件）→ 3 连败工具禁用 → 第 4 轮仍调工具 → no_tools。


def _bad_args_skill_script(n=4):
    """生成 n 轮「非法参数调 run_report」的 LLM 回放脚本。"""
    return [{"role": "assistant", "content": "",
             "tool_calls": [_call("run_report", "not-json{{", cid=f"c{i}")]}
            for i in range(n)]


def test_e2e_failed_skill_triggers_improve_card_and_apply(e2e):
    """失败全链：skill 连败 → no_tools → 诊断发卡 → 模拟批准 → SKILL.md 写回。"""
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from gateway.app import create_app, process_card_payload
    from orchestrator.coding.skill_diagnoser import SkillDiagnoser
    from persistence.models import Base

    suggestion = {"skill": "reportgen", "issue": "参数说明不清导致模型传错 JSON",
                  "fix": "在 SKILL.md 补充参数示例", "file": "SKILL.md",
                  "patch": "### 参数示例\n`{\"tag\": \"daily\"}`"}
    llm = FakeLLM(_bad_args_skill_script(),
                  chat_response=json.dumps(suggestion, ensure_ascii=False))
    runner = e2e["runner"]
    runner.llm = llm
    runner.diagnoser = SkillDiagnoser(llm=llm, skills_dir=e2e["tmp"] / "skills")

    r = runner.run_sync(FakeIncoming("/code 生成报表"), "生成报表")

    # 3 连败 → 工具禁用 → 仍调工具 → no_tools；诊断发出 skill_improve 卡
    assert r["status"] == "no_tools"
    cards = [c.args[1] for c in e2e["im"].send_card.call_args_list]
    assert len(cards) == 1
    value = cards[0]["elements"][-1]["actions"][0]["value"]
    assert value["action"] == "skill_improve"
    body = cards[0]["elements"][0]["text"]["content"]
    assert "reportgen" in body and "SKILL.md" in body

    # 模拟用户在飞书点「批准写回」：走真实 gateway 卡片管线
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = create_app(secret="s",
                     orchestrator=SimpleNamespace(coding_runner=runner),
                     approval_broker=e2e["broker"])
    app.state.session_factory = sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False)
    resp = process_card_payload(app, {**value, "open_id": value["owner"]})

    assert resp["ok"] is True and resp["status"] == "applied"
    md = (e2e["tmp"] / "skills" / "reportgen" / "SKILL.md").read_text(encoding="utf-8")
    assert "## 改进记录" in md and "参数示例" in md
    assert (e2e["tmp"] / "skills" / "reportgen" / "SKILL.md.bak").exists()


def test_e2e_final_after_tools_disabled_still_diagnoses(e2e):
    """连败禁用后模型文字收尾（final）→ 仍触发诊断卡（Phase 27 真机修复）。

    真机发现：连败禁用后模型按引导文字总结（final），旧触发条件
    status != "final" 把这条最常见路径漏掉了。修复后 final+tools_disabled
    也诊断；"先失败后成功"（无连败禁用）仍不诊断。
    """
    from orchestrator.coding.skill_diagnoser import SkillDiagnoser

    suggestion = {"skill": "reportgen", "issue": "参数说明不清",
                  "fix": "补充参数示例", "file": "SKILL.md",
                  "patch": "### 参数示例\n`{\"tag\": \"daily\"}`"}
    # 3 轮非法参数连败 + 第 4 轮按引导文字收尾 → final + tools_disabled
    script = _bad_args_skill_script(3)
    script.append({"role": "assistant", "content": "工具被禁用，文字总结", "tool_calls": None})
    llm = FakeLLM(script, chat_response=json.dumps(suggestion, ensure_ascii=False))
    runner = e2e["runner"]
    runner.llm = llm
    runner.diagnoser = SkillDiagnoser(llm=llm, skills_dir=e2e["tmp"] / "skills")

    r = runner.run_sync(FakeIncoming("/code 生成报表"), "生成报表")

    assert r["status"] == "final"
    cards = [c.args[1] for c in e2e["im"].send_card.call_args_list]
    assert len(cards) == 1
    value = cards[0]["elements"][-1]["actions"][0]["value"]
    assert value["action"] == "skill_improve"


def test_e2e_crashed_skill_subprocess_triggers_diagnose(e2e):
    """子进程真失败全链：合法参数调 crash 工具 → SCRIPT_ERROR（透传修复后
    真实 ok=False）→ 连败禁用 → no_tools → 诊断发卡 → 批准写回。"""
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from gateway.app import create_app, process_card_payload
    from orchestrator.coding.skill_diagnoser import SkillDiagnoser
    from persistence.models import Base

    # 追加一个必然崩溃的 skill（非零退出 + stderr）
    crash = e2e["tmp"] / "skills" / "crashskill"
    crash.mkdir(parents=True)
    (crash / "SKILL.md").write_text(
        "---\nname: crashskill\ndescription: 崩溃工具 crash\n---\n"
        "调用 crash_run（会崩）。\n", encoding="utf-8")
    (crash / "tools.yaml").write_text(
        f"tools:\n"
        f"  - name: crash_run\n"
        f"    description: 必崩工具\n"
        f"    parameters: {{type: object, properties: {{}}}}\n"
        f"    command: [{Path(sys.executable).as_posix()}, -c, "
        f"\"import sys; sys.stderr.write('crash boom'); sys.exit(3)\"]\n",
        encoding="utf-8")

    # 4 轮合法 JSON 参数调 crash_run：子进程每次真失败（SCRIPT_ERROR）
    script = [{"role": "assistant", "content": "",
               "tool_calls": [_call("crash_run", "{}", cid=f"c{i}")]}
              for i in range(4)]
    suggestion = {"skill": "crashskill", "issue": "脚本本身崩溃（exit 3）",
                  "fix": "修复脚本退出逻辑", "file": "SKILL.md",
                  "patch": "### 已知问题\n脚本 exit 3，待修复"}
    llm = FakeLLM(script, chat_response=json.dumps(suggestion, ensure_ascii=False))
    runner = e2e["runner"]
    runner.llm = llm
    runner.diagnoser = SkillDiagnoser(llm=llm, skills_dir=e2e["tmp"] / "skills")

    # 持续批准 L2 审批卡（只批 code_approval；skill_improve 卡留给后面手动走）
    import threading
    stop = threading.Event()

    def auto_approve_all():
        seen = 0
        while not stop.is_set():
            calls = e2e["im"].send_card.call_args_list
            for c in calls[seen:]:
                value = c.args[1]["elements"][-1]["actions"][0]["value"]
                if value.get("action") == "code_approval":
                    e2e["broker"].decide(value["code_approval_id"], "approve",
                                         operator=value["owner"])
            seen = len(calls)
            threading.Event().wait(0.02)

    approver = threading.Thread(target=auto_approve_all, daemon=True)
    approver.start()
    r = runner.run_sync(FakeIncoming("/code 跑崩溃工具"), "跑崩溃工具")
    stop.set()
    approver.join(timeout=5)

    assert r["status"] == "no_tools"
    # 关键断言：首次失败观察含 SCRIPT_ERROR（ToolResult 透传修复生效，
    # 修复前会被包成 {"result": "ToolResult(...)"} 且 ok=True）
    first_tool_msg = [m for m in llm.calls[1]["messages"]
                      if m["role"] == "tool"][-1]
    assert "SCRIPT_ERROR" in first_tool_msg["content"]
    # 诊断卡 → 批准 → crashskill SKILL.md 写回
    cards = [c.args[1] for c in e2e["im"].send_card.call_args_list]
    improve_cards = [
        c for c in cards
        if c["elements"][-1]["actions"][0]["value"].get("action") == "skill_improve"
    ]
    assert len(improve_cards) == 1
    value = improve_cards[0]["elements"][-1]["actions"][0]["value"]
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = create_app(secret="s",
                     orchestrator=SimpleNamespace(coding_runner=runner),
                     approval_broker=e2e["broker"])
    app.state.session_factory = sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False)
    resp = process_card_payload(app, {**value, "open_id": value["owner"]})
    assert resp["ok"] is True and resp["status"] == "applied"
    md = (crash / "SKILL.md").read_text(encoding="utf-8")
    assert "## 改进记录" in md and "exit 3" in md
    assert (crash / "SKILL.md.bak").exists()


def test_e2e_final_status_skips_diagnose(e2e):
    """任务成功（final）：已装配 diagnoser 也不触发诊断。"""
    llm = FakeLLM([
        {"role": "assistant", "content": "直接回答，无需工具", "tool_calls": None},
    ])
    runner = e2e["runner"]
    runner.llm = llm
    runner.diagnoser = MagicMock()
    r = runner.run_sync(FakeIncoming("/code 聊一句"), "聊一句")
    assert r["status"] == "final"
    runner.diagnoser.diagnose.assert_not_called()
    assert not e2e["im"].send_card.called


def test_e2e_diagnose_exception_does_not_break_run(e2e):
    """diagnoser 抛异常：run_sync 静默吞掉，主流程返回不受影响。"""
    llm = FakeLLM(_bad_args_skill_script())
    runner = e2e["runner"]
    runner.llm = llm
    runner.diagnoser = MagicMock()
    runner.diagnoser.diagnose.side_effect = RuntimeError("boom")

    r = runner.run_sync(FakeIncoming("/code 生成报表"), "生成报表")
    assert r["status"] == "no_tools"     # 主流程照常返回
    assert not e2e["im"].send_card.called   # 诊断失败 → 无 skill_improve 卡
