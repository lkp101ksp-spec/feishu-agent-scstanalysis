# Phase 26 T7：CodingRunner + 装配

> 前置阅读：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（共享约定）
> 前置任务：T1-T6 全部就绪
> 背景：spec §2/§6——CodingRunner 为 /code 入口：受理即回 + 后台线程跑 AgentLoop；工具面三层拼装（CodeTools 原语 + skill 工具 + registry 白名单 `sc_*`）；L2 审批发卡（value 内嵌 owner，回调 T6 分支决策）；过程反馈 v1 节流文本（IMAdapter 无 update_card，卡片更新留 v2）。

**Files:**
- Create: `orchestrator/coding/coding_runner.py`
- Modify: `gateway/runtime.py`（build_runtime 装配 coding_runner 挂到 orchestrator）
- Test: `tests/unit/test_coding_runner.py`

**已确认的现有接口**（勿改）：
- `research_runner.handle` 模式：剥前缀 → 无参回用法 → `threading.Thread(target=self._run, daemon=True)` → 返回 `{"status": "research_accepted", ...}`
- `ToolHandler.execute(tool_name, inputs, *, actor_open_id="", session_id="") -> ToolResult`——**不拦 L2**（L92-94 注释：L2 审批上移调用方），skill 工具可经它统一分发
- `ApprovalBroker.wait(id, timeout) -> "approve"/"deny"/None`；`decide(id, decision, operator) -> bool`
- `IMAdapter.reply(chat_id, text)` / `send_card(chat_id, card_json)`（无 update_card）
- `shared.ulid_.new_ulid()`
- `ToolSpec.to_openai_function()`

**接口契约**：
- `CodingRunner(*, llm, im, tool_handler, registry, broker, settings=None)`——llm 需实现 `chat_with_tools`（T1）
- `.handle(incoming: IncomingMessage) -> dict`——status ∈ `coding_usage / coding_accepted / coding_cleared`
- `.run_sync(incoming, task_text) -> dict`——线程主体（测试直调，不起线程）
- 会话 ID：`code_{zlib.crc32(chat_id):08x}`（同 chat 复用工作区，跨任务持久）；`/code clear` 清空

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_runner.py -v --basetemp=.pytest_basetemp`
Expected: collection FAIL（`ModuleNotFoundError: orchestrator.coding.coding_runner`）

- [ ] **Step 3: 实现**

创建 `orchestrator/coding/coding_runner.py`：

```python
"""CodingRunner：/code 指令入口 + 工具面拼装 + 后台执行（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §2/§6：
- handle 受理即回，线程跑 run_sync（AgentLoop 全链）
- 工具面三层：CodeTools 原语 + skills/ 热加载 L2 工具 + registry 白名单（sc_*）
- L2 审批发卡（value 内嵌 owner，回调 code_approval 分支决策，不落库）
- 过程反馈 v1：on_step 节流文本（卡片更新留 v2——IMAdapter 无 update_card）
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import zlib
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Optional

from orchestrator.coding.agent_loop import AgentLoop, LoopResult
from orchestrator.coding.code_tools import CodeTools
from orchestrator.coding.skill_loader import SkillLoader
from orchestrator.coding.workspace import WorkspaceManager
from orchestrator.tools.tool_handler import ToolHandler, ToolResult
from orchestrator.tools.tool_registry import ToolRegistry
from shared.ulid_ import new_ulid

logger = logging.getLogger(__name__)

_USAGE_MSG = (
    "「/code」用法：/code <任务描述>\n"
    "例如：/code 写一个快速排序并自测\n"
    "清除会话工作区：/code clear"
)

# code 原语名集合（dispatch 分流用）
_CODE_PRIMITIVES = {t["function"]["name"] for t in CodeTools.SCHEMA}

_SYSTEM_PROMPT = """你是飞书后台 coding agent，在用户的本机会话工作区内完成任务。

工作规范：
1. 优先用工具读写/修改工作区文件，用 run_cmd 执行与验证（python/pytest 等白名单命令直接执行）。
2. 非白名单命令（如网络下载、git 写操作）会触发用户审批卡片，等待用户点击后继续。
3. 每步观察失败时先读错误再修；连续失败会导致工具被临时禁用。
4. 完成后用简洁中文总结：做了什么、关键结果、产物文件路径。
安全约束：所有文件操作仅限当前工作区；不要尝试访问工作区外路径。"""


class _ProgressReporter:
    """on_step 节流器：每 N 个工具事件一行进度；关键事件即时发。"""

    def __init__(self, im, chat_id: str, every: int = 3) -> None:
        self.im = im
        self.chat_id = chat_id
        self.every = every
        self._n = 0

    def __call__(self, step: int, event: dict) -> None:
        kind = event.get("event")
        if kind == "tool":
            self._n += 1
            if self._n % self.every == 0:
                mark = "✓" if event.get("ok") else "✗"
                self._safe(f"[进行中] step {step} · {mark} {event.get('name')}")
        elif kind == "tools_disabled":
            self._safe(f"[进行中] step {step} · 工具连续失败已临时禁用")
        elif kind == "compressed":
            self._safe(f"[进行中] step {step} · 上下文已压缩")

    def _safe(self, text: str) -> None:
        """回复失败不影响主流程。"""
        try:
            self.im.reply(self.chat_id, text)
        except Exception:  # noqa: BLE001
            logger.exception("progress reply failed")


def _toolresult_to_dict(tr: ToolResult) -> dict:
    """ToolResult → AgentLoop 观察 dict。"""
    if tr.error_code:
        return {"ok": False, "error": f"{tr.error_code}: {tr.error_message}"}
    out = dict(tr.outputs or {})
    out["ok"] = True
    return out


def _approval_card(approval_id: str, owner: str, info: list[str]) -> dict:
    """code 审批卡：value 内嵌 owner（回调比对不查库）。"""
    lines = "\n".join(f"- {x}" for x in info) or "-（无详情）"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "/code 操作审批"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": lines}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "批准"},
                 "type": "primary",
                 "value": {"action": "code_approval", "code_approval_id": approval_id,
                           "decision": "approve", "owner": owner}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "拒绝"},
                 "type": "danger",
                 "value": {"action": "code_approval", "code_approval_id": approval_id,
                           "decision": "deny", "owner": owner}},
            ]},
        ],
    }


class CodingRunner:
    """/code 指令入口：受理 + 后台线程驱动 AgentLoop。"""

    def __init__(self, *, llm, im, tool_handler: ToolHandler,
                 registry: ToolRegistry, broker, settings=None) -> None:
        self.llm = llm
        self.im = im
        self.tool_handler = tool_handler
        self.registry = registry
        self.broker = broker
        s = settings
        self.ws = WorkspaceManager(Path(getattr(s, "code_workspace_root", "./code_workspace")))
        self.skills_dir = Path(getattr(s, "code_skills_dir", "./skills"))
        self.max_steps = int(getattr(s, "code_max_steps", 25))
        self.token_budget = int(getattr(s, "code_token_budget", 200_000))
        self.timeout_sec = int(getattr(s, "code_timeout_sec", 3600))
        self.model = (getattr(s, "code_model", "") or "").strip() or None
        self.registry_prefixes = [p.strip() for p in
                                  getattr(s, "code_registry_tools", "sc_*").split(",")
                                  if p.strip()]
        # 审批等待复用 research 的超时配置
        self.approval_timeout_sec = int(getattr(s, "research_approval_timeout_sec", 600))

    # ------------------------------------------------------------------ #
    def handle(self, incoming) -> dict:
        """process() 的 /code 分支入口：受理即回 + 后台线程执行。"""
        task_text = incoming.text.strip()[len("/code"):].strip()
        if not task_text:
            self.im.reply(incoming.chat_id, _USAGE_MSG)
            return {"status": "coding_usage"}
        if task_text == "clear":
            session_id = self._session_id(incoming.chat_id)
            shutil.rmtree(self.ws.session_dir(session_id), ignore_errors=True)
            self.im.reply(incoming.chat_id, f"[完成] 会话工作区已清空（{session_id}）")
            return {"status": "coding_cleared", "session_id": session_id}
        t = threading.Thread(target=self._thread_body,
                             args=(incoming, task_text), daemon=True)
        t.start()
        return {"status": "coding_accepted", "task_text": task_text}

    def _thread_body(self, incoming, task_text: str) -> None:
        """后台线程主体：异常兜底回复。"""
        try:
            self.run_sync(incoming, task_text)
        except Exception:  # noqa: BLE001
            logger.exception("coding task crashed")
            try:
                self.im.reply(incoming.chat_id, "[错误] coding 任务异常终止，详见服务端日志")
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    def run_sync(self, incoming, task_text: str) -> dict:
        """同步执行全链（测试直调）；返回 LoopResult 摘要 dict。"""
        session_id = self._session_id(incoming.chat_id)
        approve_fn = self._make_approve_fn(incoming)
        code_tools = CodeTools(self.ws, session_id, approve_fn=approve_fn)

        # skill 热加载：每次任务 scan 一次（拾取新增目录）
        loader = SkillLoader(self.skills_dir)
        loader.scan()
        skill_tool_names = [t["name"] for sk in loader.skills for t in sk.tools]
        loader.register_tools(self.registry)

        # registry 白名单（前缀通配）+ skill 工具 → schema 与 risk_map
        whitelist = [t for t in self.registry.list(planner_visible=True)
                     if any(fnmatch(t.name, p) for p in self.registry_prefixes)]
        extra_names = {t.name for t in whitelist} | set(skill_tool_names)
        specs = [self.registry.get(n) for n in sorted(extra_names)]
        tools_schema = list(CodeTools.SCHEMA) + [s.to_openai_function() for s in specs]
        risk_map = {n: "L1" for n in _CODE_PRIMITIVES}       # run_cmd 审批在 CodeTools 内部
        risk_map.update({s.name: s.risk_level for s in specs})

        dispatch = self._make_dispatch(code_tools, session_id, incoming.sender_open_id)
        knowledge = loader.build_system_knowledge(task_text)
        system = _SYSTEM_PROMPT + (f"\n\n{knowledge}" if knowledge else "")

        reporter = _ProgressReporter(self.im, incoming.chat_id)
        loop = AgentLoop(self.llm, tools_schema, dispatch,
                         max_steps=self.max_steps, token_budget=self.token_budget,
                         timeout_sec=self.timeout_sec, risk_map=risk_map,
                         approve_fn=approve_fn, on_step=reporter, model=self.model)
        try:
            result = loop.run(system, task_text)
        except Exception:  # noqa: BLE001 —— sync 全链兜底（线程体只做日志）
            logger.exception("agent loop crashed")
            self.im.reply(incoming.chat_id, "[错误] coding 任务异常终止，详见服务端日志")
            return {"status": "error", "steps": 0, "final_text": ""}
        self.im.reply(incoming.chat_id, _render_result(result))
        return {"status": result.status, "steps": result.steps,
                "final_text": result.final_text}

    # ------------------------------------------------------------------ #
    def _session_id(self, chat_id: str) -> str:
        """同 chat 稳定会话 ID（工作区跨任务持久）。"""
        return f"code_{zlib.crc32(chat_id.encode('utf-8')):08x}"

    def _make_approve_fn(self, incoming) -> Callable[[list[str]], bool]:
        """构造审批闭包：发卡（内嵌 owner）→ broker.wait → approve 判定。"""
        chat_id = incoming.chat_id
        owner = incoming.sender_open_id

        def approve_fn(info: list[str]) -> bool:
            approval_id = new_ulid()
            try:
                self.im.send_card(chat_id, _approval_card(approval_id, owner, info))
            except Exception:  # noqa: BLE001 —— 发卡失败按拒绝处理
                logger.exception("approval card send failed")
                return False
            decision = self.broker.wait(approval_id, timeout=self.approval_timeout_sec)
            logger.info("code approval %s -> %s", approval_id, decision)
            return decision == "approve"

        return approve_fn

    def _make_dispatch(self, code_tools: CodeTools, session_id: str,
                       actor_open_id: str) -> Callable[[str, object], dict]:
        """统一分发：code 原语走 CodeTools，其余走 ToolHandler.execute。"""

        def dispatch(name: str, arguments) -> dict:
            if name in _CODE_PRIMITIVES:
                return code_tools.dispatch(name, arguments)
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError:
                    return {"ok": False, "error": f"BAD_ARGS: {arguments[:100]}"}
            tr = self.tool_handler.execute(
                name, arguments, actor_open_id=actor_open_id, session_id=session_id)
            return _toolresult_to_dict(tr)

        return dispatch


def _render_result(result: LoopResult) -> str:
    """LoopResult → 结果回复文本。"""
    ok_n = sum(1 for e in result.tool_events if e["ok"])
    fail_n = len(result.tool_events) - ok_n
    labels = {"final": "完成", "max_steps": "步数耗尽",
              "budget_exhausted": "上下文预算耗尽", "timeout": "超时",
              "no_tools": "工具不可用"}
    head = (f"[coding {labels.get(result.status, result.status)}] "
            f"步数 {result.steps} · 工具 {ok_n}✓/{fail_n}✗ · ~{result.approx_tokens} tokens")
    body = result.final_text or result.abort_reason or "（无输出）"
    return f"{head}\n\n{body}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_runner.py -v --basetemp=.pytest_basetemp`
Expected: 全 PASS（12 用例）

- [ ] **Step 5: build_runtime 装配**

`gateway/runtime.py` `build_runtime()`：在 research_runner 装配之后（coding_runner 依赖的 llm/im/tool_handler/registry/broker 均已就绪处）追加：

```python
    # Phase 26：/code agentic coding 链路
    from orchestrator.coding.coding_runner import CodingRunner
    orch.coding_runner = CodingRunner(
        llm=llm_router, im=im_adapter, tool_handler=tool_handler,
        registry=registry, broker=approval_broker, settings=settings,
    )
```

变量名以 `build_runtime` 内实际命名为准（llm_router/im_adapter/tool_handler/registry/approval_broker/settings 的既有局部变量名），保持其余装配不动。

- [ ] **Step 6: 冒烟（装配后全链 import 无环 + 既有回归不破）**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_runner.py tests/integration/test_message_flow.py -v --basetemp=.pytest_basetemp`
Expected: 全 PASS

- [ ] **Step 7: commit**

```bash
git add orchestrator/coding/coding_runner.py gateway/runtime.py tests/unit/test_coding_runner.py
git commit -m "feat(phase26): CodingRunner 三层工具面拼装 + build_runtime 装配"
```

完成后删除 `.pytest_basetemp`，回总览勾选 T7。
