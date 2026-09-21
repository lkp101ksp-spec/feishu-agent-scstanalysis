"""CodingRunner：/code 指令入口 + 工具面拼装 + 后台执行（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §2/§6：
- handle 受理即回，线程跑 run_sync（AgentLoop 全链）
- 工具面三层：CodeTools 原语 + skills/ 热加载 L2 工具 + registry 白名单（sc_*）
- L2 审批发卡（value 内嵌 owner，回调 code_approval 分支决策，不落库）
- 过程反馈 v2（Phase 39）：受理发进度卡，on_step 节流原地刷新
  （IMAdapter.update_card / PATCH message），终态定格；发卡失败或无
  message_id（CLI 路径/老部署）自动回退 v1 节流文本，主流程绝不受影响。
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import zlib
from collections import deque
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from orchestrator.coding.agent_loop import AgentLoop, LoopResult
from orchestrator.coding.code_tools import CodeTools
from orchestrator.coding.skill_loader import SkillLoader
from orchestrator.coding.workspace import WorkspaceManager
from orchestrator.tools.tool_handler import ToolHandler, ToolResult
from orchestrator.tools.tool_registry import ToolRegistry
from shared.ulid_ import new_ulid

if TYPE_CHECKING:
    from orchestrator.approval_broker import ApprovalBroker
    from orchestrator.coding.skill_diagnoser import SkillDiagnoser
    from orchestrator.coding.skill_distiller import SkillDistiller
    from orchestrator.coding.skill_installer import SkillInstaller
    from shared.schemas import IncomingMessage

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

    def __init__(self, im: Any, chat_id: str, every: int = 3) -> None:
        self.im = im
        self.chat_id = chat_id
        self.every = every
        self._n = 0

    def __call__(self, step: int, event: dict[str, Any]) -> None:
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

    def finish(self, result: LoopResult) -> None:
        """v1 无终态动作（接口与 _ProgressCard 对齐，终态由结果文本承担）。"""

    def finish_error(self) -> None:
        """v1 无终态动作（接口与 _ProgressCard 对齐）。"""


_TASK_PREVIEW_CAP = 120     # 进度卡任务描述预览长度
_FINAL_PREVIEW_CAP = 300    # 终态卡最终答复预览长度
_EVENT_KEEP = 5             # 卡面保留的最近事件条数


def _fmt_elapsed(sec: float) -> str:
    """耗时人性化：90 秒内显示秒，否则分。"""
    return f"{sec:.0f}s" if sec < 90 else f"{sec / 60:.1f}min"


class _ProgressCard:
    """on_step 进度卡 v2：受理发一张卡，节流原地刷新，终态定格。

    节流双条件：每 every 个工具事件且距上次刷新 ≥ min_interval_sec（PATCH
    限频护栏）；关键事件（tools_disabled/compressed）即时刷新（仍受频率
    护栏约束）。update_card 任何失败 → broken 停止后续刷新（卡面停在最后
    状态，终态结果仍以文本兜底回复），主流程绝不受影响。
    """

    def __init__(self, im: Any, chat_id: str, task_text: str,
                 every: int = 3, min_interval_sec: float = 2.0,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.im = im
        self.chat_id = chat_id
        self.task_text = task_text
        self.every = every
        self.min_interval = min_interval_sec
        self._now = now  # 时间注入（测试可控，同 IntentGateService 惯例）
        self._msg_id = ""
        self._t0 = 0.0
        self._last = 0.0
        self._n = 0
        self._step = 0
        self._events: deque[str] = deque(maxlen=_EVENT_KEEP)
        self._broken = False

    def start(self) -> bool:
        """发出初始进度卡；成功且拿到 message_id → True（走卡片路径）。"""
        self._t0 = self._now()
        try:
            self._msg_id = self.im.send_card(self.chat_id, self._render(0))
        except Exception:  # noqa: BLE001 —— 发卡失败回退 v1 文本节流
            logger.exception("progress card send failed (fallback v1)")
            return False
        return bool(self._msg_id)

    def __call__(self, step: int, event: dict[str, Any]) -> None:
        if self._broken or not self._msg_id:
            return
        kind = event.get("event")
        if kind == "tool":
            self._n += 1
            mark = "✓" if event.get("ok") else "✗"
            self._events.append(f"{mark} {event.get('name')}")
            if self._n % self.every != 0:
                return
        elif kind == "tools_disabled":
            self._events.append("⚠ 工具连续失败已临时禁用")
        elif kind == "compressed":
            self._events.append("⚠ 上下文已压缩")
        else:
            return
        self._step = step
        now = self._now()
        if now - self._last < self.min_interval:
            return
        self._update()

    def finish(self, result: LoopResult) -> None:
        """终态定格：完成/未完全成功 + steps + 耗时 + 最终答复预览。"""
        if self._broken or not self._msg_id:
            return
        try:
            self.im.update_card(self._msg_id, self._render(self._step,
                                                           result=result))
        except Exception:  # noqa: BLE001
            logger.exception("progress card finish update failed (ignored)")

    def finish_error(self) -> None:
        """异常终止定格（AgentLoop 崩溃路径）。"""
        if self._broken or not self._msg_id:
            return
        try:
            self.im.update_card(self._msg_id, self._render(
                self._step, error="任务异常终止，详见服务端日志"))
        except Exception:  # noqa: BLE001
            logger.exception("progress card error update failed (ignored)")

    def _update(self) -> None:
        try:
            self.im.update_card(self._msg_id, self._render(self._step))
            self._last = self._now()
        except Exception:  # noqa: BLE001 —— 停更保主流程（卡面停最后状态）
            logger.exception("progress card update failed (stop updating)")
            self._broken = True

    def _render(self, step: int, result: LoopResult | None = None,
                error: str = "") -> dict[str, Any]:
        """卡面渲染：进行中（任务预览 + 最近事件 + 耗时）/ 终态两形态。"""
        preview = self.task_text[:_TASK_PREVIEW_CAP] + (
            "…" if len(self.task_text) > _TASK_PREVIEW_CAP else "")
        if result is None and not error:
            lines = [f"**任务**：{preview}", ""]
            if self._events:
                lines.append("**最近事件**：")
                lines += [f"- {e}" for e in self._events]
                lines.append("")
            lines.append(f"⏱ 已运行 {_fmt_elapsed(self._now() - self._t0)}"
                         f" · step {step}")
            return {"header": f"代码任务进行中 · step {step}",
                    "elements": [{"tag": "div", "text": {
                        "tag": "lark_md", "content": "\n".join(lines)}}]}
        if error:
            return {"header": "代码任务异常终止",
                    "elements": [{"tag": "div", "text": {
                        "tag": "lark_md", "content":
                            f"**任务**：{preview}\n\n❌ {error}"}}]}
        if result is None:  # 防御分支：result/error 双空上面已返回，此处理论不可达
            return {"header": "代码任务异常终止",
                    "elements": [{"tag": "div", "text": {
                        "tag": "lark_md", "content":
                            f"**任务**：{preview}\n\n❌ 任务未产出结果"}}]}
        ok = result.status == "final"
        final_preview = (result.final_text or "")[:_FINAL_PREVIEW_CAP]
        lines = [f"**任务**：{preview}", "",
                 f"{'✅ 完成' if ok else '⚠ 结束（未完全成功）'} · "
                 f"{result.steps} steps · 耗时 "
                 f"{_fmt_elapsed(self._now() - self._t0)}"]
        if final_preview:
            lines += ["", f"> {final_preview}"]
        return {"header": "代码任务已完成" if ok else "代码任务结束",
                "elements": [{"tag": "div", "text": {
                    "tag": "lark_md", "content": "\n".join(lines)}}]}


def _toolresult_to_dict(tr: ToolResult) -> dict[str, Any]:
    """ToolResult → AgentLoop 观察 dict。"""
    if tr.error_code:
        return {"ok": False, "error": f"{tr.error_code}: {tr.error_message}"}
    out = dict(tr.outputs or {})
    out["ok"] = True
    return out


def _approval_card(approval_id: str, owner: str, info: list[str]) -> dict[str, Any]:
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


def _cap_suggestion(suggestion: dict[str, Any]) -> dict[str, Any]:
    """裁剪 suggestion 字段长度，保证按钮 value 内嵌 JSON 不超飞书长度限制。"""
    return {k: str(suggestion.get(k, ""))[:cap]
            for k, cap in _SUGGESTION_CAPS.items()}


def _skill_improve_card(owner: str, suggestion: dict[str, Any]) -> dict[str, Any]:
    """skill 改进/沉淀审批卡（Phase 27 patch / Phase 77 create 双 kind）。

    create 型 files 全文不内嵌按钮 value（长度限制）——卡片展示 files 键
    清单，value 内嵌的 suggestion 只带 files 键 list；完整内容经
    CodingRunner._pending_create[improve_id] 进程内暂存，回调按 id 取回。
    """
    improve_id = new_ulid()
    kind = str(suggestion.get("kind", "patch"))
    base_value = {"action": "skill_improve", "skill_improve_id": improve_id,
                  "owner": owner, "kind": kind}
    if kind == "create":
        files = suggestion.get("files") or {}
        file_keys = sorted(files) if isinstance(files, dict) else []
        md_preview = str(files.get("SKILL.md", ""))[:300]
        capped: dict[str, Any] = {"skill": str(suggestion.get("skill", ""))[:100],
                  "issue": str(suggestion.get("issue", ""))[:300],
                  "fix": str(suggestion.get("fix", ""))[:300],
                  "kind": "create", "files": file_keys}
        lines = "\n".join([
            f"- 新 skill：**{capped['skill']}**（沉淀新建）",
            f"- 重复模式：{capped['issue']}",
            f"- 价值：{capped['fix']}",
            f"- 文件：{', '.join(file_keys)}",
            f"- SKILL.md 预览：{md_preview}{'…' if len(str(files.get('SKILL.md','')))>300 else ''}",
        ])
        title = "/code skill 沉淀审批（新建）"
    else:
        capped = _cap_suggestion(suggestion)
        capped["kind"] = "patch"
        patch_preview = capped["patch"][:200] + ("…" if len(capped["patch"]) > 200 else "")
        lines = "\n".join([
            f"- skill：**{capped['skill']}**",
            f"- 问题：{capped['issue']}",
            f"- 建议：{capped['fix']}",
            f"- 目标文件：{capped['file']}",
            f"- patch 预览：{patch_preview or '（空）'}",
        ])
        title = "/code skill 改进审批"
    base_value["suggestion"] = json.dumps(capped, ensure_ascii=False)
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}},
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

    def __init__(self, *, llm: Any, im: Any, tool_handler: ToolHandler,
                 registry: ToolRegistry, broker: ApprovalBroker, settings: Any = None,
                 diagnoser: "SkillDiagnoser | None" = None,
                 distiller: "SkillDistiller | None" = None,
                 installer: "SkillInstaller | None" = None) -> None:
        """diagnoser 为 Phase 27 SkillDiagnoser（可空，None 时跳过失败诊断）；
        distiller/installer 为 Phase 77 复盘器/落盘层（可空，None 时跳过成功复盘）。"""
        self.llm = llm
        self.im = im
        self.tool_handler = tool_handler
        self.registry = registry
        self.broker = broker
        self.diagnoser = diagnoser
        self.distiller = distiller
        self.installer = installer
        # create 型完整 suggestion 进程内暂存（improve_id → suggestion），
        # 回调按 id 取回（不落库，进程重启卡片失效点不动——code_approval 同款口径）
        self._pending_create: dict[str, dict[str, Any]] = {}
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
        # Phase 43：/code 画像注入用（对齐 research_runner Phase 42；空 = 功能关闭）
        self.bio_workspace_root = getattr(s, "bio_workspace_root", "")

    # ------------------------------------------------------------------ #
    def handle(self, incoming: IncomingMessage) -> dict[str, Any]:
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

    def _thread_body(self, incoming: IncomingMessage, task_text: str) -> None:
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
    def run_sync(self, incoming: IncomingMessage, task_text: str) -> dict[str, Any]:
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
        knowledge = loader.build_system_knowledge(task_text, llm=self.llm)
        system = _SYSTEM_PROMPT + (f"\n\n{knowledge}" if knowledge else "")
        # Phase 43：数据画像注入——任务文本含 dataset_ref 时附真实统计，
        # 防 LLM 调 sc_qc 套默认 min_genes=600 致全过滤（对齐 research_runner Phase 42）。
        try:
            from orchestrator.tools.bio.dataset_profile import build_profile_context
            profile_ctx = build_profile_context(task_text, self.bio_workspace_root)
            if profile_ctx:
                system += "\n\n" + profile_ctx
        except Exception:  # noqa: BLE001
            logger.warning("dataset profile inject failed", exc_info=True)

        reporter = self._make_reporter(incoming, task_text)
        loop = AgentLoop(self.llm, tools_schema, dispatch,
                         max_steps=self.max_steps, token_budget=self.token_budget,
                         timeout_sec=self.timeout_sec, risk_map=risk_map,
                         approve_fn=approve_fn, on_step=reporter, model=self.model)
        try:
            result = loop.run(system, task_text)
        except Exception:  # noqa: BLE001 —— sync 全链兜底（线程体只做日志）
            logger.exception("agent loop crashed")
            reporter.finish_error()
            self.im.reply(incoming.chat_id, "[错误] coding 任务异常终止，详见服务端日志")
            return {"status": "error", "steps": 0, "final_text": ""}
        reporter.finish(result)
        self.im.reply(incoming.chat_id, _render_result(result))
        # Phase 27：任务失败时自动诊断 skill 并发出改进审批卡（纯增量，失败静默）
        self._maybe_diagnose_skill(incoming, result, task_text)
        # Phase 77：任务成功时复盘沉淀新 skill（纯增量，失败静默）
        self._maybe_distill_skill(incoming, result, task_text)
        return {"status": result.status, "steps": result.steps,
                "final_text": result.final_text}

    # ------------------------------------------------------------------ #
    def _maybe_diagnose_skill(self, incoming: IncomingMessage, result: LoopResult,
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
    def _maybe_distill_skill(self, incoming: IncomingMessage, result: LoopResult,
                             task_text: str) -> None:
        """成功轨迹 → SkillDistiller 复盘 → create 审批卡；任何异常只记日志。

        触发条件（与诊断互补）：status == final 且无连败禁用（真正成功）。
        复盘是附加能力，绝不影响主流程。
        """
        if self.distiller is None:
            return
        if result.status != "final" or result.tools_disabled:
            return
        try:
            suggestion = self.distiller.distill(result, task_text)
            if not (suggestion.get("ok") and suggestion.get("skill")):
                logger.info("skill distill skipped: %s",
                            suggestion.get("reason", "no suggestion"))
                return
            card = _skill_improve_card(incoming.sender_open_id, suggestion)
            improve_id = card["elements"][1]["actions"][0]["value"]["skill_improve_id"]
            self._pending_create[improve_id] = suggestion
            self.im.send_card(incoming.chat_id, card)
        except Exception:  # noqa: BLE001 —— 复盘是附加能力，绝不影响主流程
            logger.exception("skill distill/card failed (ignored)")

    # ------------------------------------------------------------------ #
    def _make_reporter(self, incoming: IncomingMessage,
                       task_text: str) -> "_ProgressCard | _ProgressReporter":
        """进度反馈器工厂（Phase 39）：优先 v2 进度卡；发卡失败或无
        message_id（CLI 路径/老部署）自动回退 v1 节流文本。"""
        card = _ProgressCard(self.im, incoming.chat_id, task_text)
        if card.start():
            return card
        return _ProgressReporter(self.im, incoming.chat_id)

    def _session_id(self, chat_id: str) -> str:
        """同 chat 稳定会话 ID（工作区跨任务持久）。"""
        return f"code_{zlib.crc32(chat_id.encode('utf-8')):08x}"

    def _make_approve_fn(self, incoming: IncomingMessage) -> Callable[[list[str]], bool]:
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
                       actor_open_id: str) -> Callable[[str, Any], dict[str, Any]]:
        """统一分发：code 原语走 CodeTools，其余走 ToolHandler.execute。"""

        def dispatch(name: str, arguments: Any) -> dict[str, Any]:
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
