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
from typing import Callable

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


# 飞书按钮 value 长度保守上限：suggestion 各字段裁剪阈值（防回调 value 超限）
_SUGGESTION_CAPS = {"skill": 100, "issue": 300, "fix": 300, "file": 20, "patch": 1500}


def _cap_suggestion(suggestion: dict) -> dict:
    """裁剪 suggestion 字段长度，保证按钮 value 内嵌 JSON 不超飞书长度限制。"""
    return {k: str(suggestion.get(k, ""))[:cap]
            for k, cap in _SUGGESTION_CAPS.items()}


def _skill_improve_card(owner: str, suggestion: dict) -> dict:
    """skill 改进审批卡（Phase 27）：value 内嵌 owner + suggestion JSON（回调不查库）。"""
    improve_id = new_ulid()
    capped = _cap_suggestion(suggestion)
    patch_preview = capped["patch"][:200] + ("…" if len(capped["patch"]) > 200 else "")
    lines = "\n".join([
        f"- skill：**{capped['skill']}**",
        f"- 问题：{capped['issue']}",
        f"- 建议：{capped['fix']}",
        f"- 目标文件：{capped['file']}",
        f"- patch 预览：{patch_preview or '（空）'}",
    ])
    base_value = {"action": "skill_improve", "skill_improve_id": improve_id,
                  "owner": owner,
                  "suggestion": json.dumps(capped, ensure_ascii=False)}
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "/code skill 改进审批"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": lines}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "批准写回"},
                 "type": "primary",
                 "value": {**base_value, "decision": "approve"}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "忽略"},
                 "type": "danger",
                 "value": {**base_value, "decision": "deny"}},
            ]},
        ],
    }


class CodingRunner:
    """/code 指令入口：受理 + 后台线程驱动 AgentLoop。"""

    def __init__(self, *, llm, im, tool_handler: ToolHandler,
                 registry: ToolRegistry, broker, settings=None,
                 diagnoser=None) -> None:
        """diagnoser 为 Phase 27 SkillDiagnoser（可空，None 时跳过失败诊断）。"""
        self.llm = llm
        self.im = im
        self.tool_handler = tool_handler
        self.registry = registry
        self.broker = broker
        self.diagnoser = diagnoser
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
        # Phase 27：任务失败时自动诊断 skill 并发出改进审批卡（纯增量，失败静默）
        self._maybe_diagnose_skill(incoming, result, task_text)
        return {"status": result.status, "steps": result.steps,
                "final_text": result.final_text}

    # ------------------------------------------------------------------ #
    def _maybe_diagnose_skill(self, incoming, result: LoopResult,
                              task_text: str) -> None:
        """失败轨迹 → SkillDiagnoser 诊断 → 改进审批卡；任何异常只记日志。

        触发条件（Phase 27 真机修复）：status != final，或 final 但发生过
        连败禁用（tools_disabled=True）——连败禁用后模型按引导文字收尾
        会得到 final，但任务实质失败，仍应诊断；"先失败后成功"的正常
        final（无禁用）不触发。
        """
        if self.diagnoser is None:
            return
        if result.status == "final" and not result.tools_disabled:
            return
        try:
            suggestion = self.diagnoser.diagnose(result, task_text)
            if not (suggestion.get("ok") and suggestion.get("skill")):
                logger.info("skill diagnose skipped: %s",
                            suggestion.get("reason", "no suggestion"))
                return
            self.im.send_card(
                incoming.chat_id,
                _skill_improve_card(incoming.sender_open_id, suggestion))
        except Exception:  # noqa: BLE001 —— 诊断是附加能力，绝不影响主流程
            logger.exception("skill diagnose/card failed (ignored)")

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
