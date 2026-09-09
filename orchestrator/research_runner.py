"""ResearchRunner：/research 研究任务后台执行器（Phase 12 板块⑤）。

在独立线程跑 Planner→Scheduler→渲染→写文档全链路，避免阻塞 ws 回调
线程；DB 落库走独立 session（复用 Phase 11 独立 session 收尾惯例：
成功 commit / 异常 rollback）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from orchestrator.planner.scheduler import Scheduler
from orchestrator.tools.tool_registry import parse_disabled_tools
from shared.executor_types import ExecutionState
from shared.schemas import IncomingMessage

if TYPE_CHECKING:
    # 仅类型标注用：斩断 lark SDK（im_adapter）等重依赖的运行时导入链
    from sqlalchemy.orm import Session

    from feishu_adapter.im_adapter import IMAdapter
    from orchestrator.approval_broker import ApprovalBroker
    from orchestrator.blocks.schemas import AnyBlock
    from orchestrator.llm_router import LLMRouter
    from orchestrator.planner.dag_schema import DAGNode, DAGPlan

logger = logging.getLogger(__name__)

_ACCEPT_MSG = (
    "[研究任务] 已受理，正在规划执行…\n"
    "（任务越具体效果越好；完成后自动回复，期间可继续聊天）"
)
_USAGE_MSG = (
    "「/research」用法：/research <任务描述>，"
    "例如：/research 总结当前绑定文档的核心内容并给出三条研究建议"
)

_TASK_PREVIEW_CAP = 120
_RECENT_KEEP = 5


def _fmt_elapsed(sec: float) -> str:
    """秒 → 人类可读耗时（12s / 5m08s / 1h02m）。"""
    sec = max(int(sec), 0)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


def _research_llm(orch: Any) -> LLMRouter | None:
    """Phase 44：/research 场景 llm——orch.research_llm 优先，缺省回退 orch.llm。"""
    llm: LLMRouter | None = (
        getattr(orch, "research_llm", None) or getattr(orch, "llm", None))
    return llm


class _ResearchProgressCard:
    """/research 进度卡（Phase 41）：受理即发卡，原地刷新，终态定格。

    与 coding _ProgressCard 同构（节流/熔断/禁用回退），事件源为
    plan/scheduler 节点状态快照而非工具事件流：
    - start()：发「规划中」卡；拿不到 message_id 或发卡异常 → _disabled
      （全程无卡，任务行为与旧版完全一致）；
    - plan_done(plan)：转执行阶段，立即刷新一次；
    - tick(snap)：观察线程周期喂快照；终态节点数变化立即刷新，否则
      min_interval 节流（防飞书 PATCH 限流）；
    - finish/finish_error：终态定格（完成/异常两类卡面）；
    - 任何 PATCH 失败 → _broken 熔断不再更新（绝不影响任务本身）。
    """

    def __init__(self, im: IMAdapter, chat_id: str, task_text: str, *,
                 min_interval: float = 8.0,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.im = im
        self.chat_id = chat_id
        self.task_text = task_text
        self.min_interval = min_interval
        self._now = now
        self._msg_id: str = ""
        self._phase = "planning"
        self._total = 0
        self._last_push = 0.0
        self._last_terminal = -1
        self._t0 = 0.0
        self._disabled = True   # start() 成功才启用（未 start 的卡全 no-op）
        self._broken = False
        self._finished = False

    # === 生命周期 ===

    def start(self) -> bool:
        """发初始「规划中」卡；成功且拿到 message_id → True。"""
        self._t0 = self._now()
        try:
            self._msg_id = self.im.send_card(
                self.chat_id, self._render_planning())
        except Exception:  # noqa: BLE001 —— 发卡失败保持禁用
            logger.exception("research progress card send failed (disabled)")
            return False
        if not self._msg_id:
            return False
        self._disabled = False
        return True

    def plan_done(self, plan: DAGPlan) -> None:
        """规划完成 → 执行阶段（记录节点总数），立即刷新一次。"""
        self._phase = "running"
        self._total = len(plan.nodes)
        self._push(self._render_running(
            {"counts": {}, "running": [], "done": [], "total": self._total}))

    def tick(self, snap: dict[str, Any]) -> None:
        """观察线程喂快照：终态数变化立即刷新，否则按 min_interval 节流。"""
        if self._disabled or self._broken or self._finished:
            return
        c = snap["counts"]
        terminal = (c.get("success", 0) + c.get("failed", 0)
                    + c.get("skipped", 0) + c.get("denied", 0))
        now = self._now()
        if (terminal != self._last_terminal
                or now - self._last_push >= self.min_interval):
            self._last_terminal = terminal
            self._push(self._render_running(snap))

    def finish(self, status: str, node_states: dict[str, str]) -> None:
        """终态定格：完成卡（状态 + 节点统计 + 耗时）。"""
        self._finished = True
        counts: dict[str, int] = {}
        for v in node_states.values():
            counts[v] = counts.get(v, 0) + 1
        stat = " ".join(
            f"{label}{counts.get(k, 0)}"
            for k, label in (("success", "✓"), ("failed", "✗"),
                             ("denied", "⊘"), ("skipped", "–")))
        card = {
            "header": "研究任务完成",
            "elements": [
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": (
                    f"**任务**：{self._task_short()}\n"
                    f"**状态**：{status} · {stat}\n"
                    f"**耗时**：{_fmt_elapsed(self._now() - self._t0)}"
                )}},
            ],
        }
        self._push(card)

    def finish_error(self, reason: str) -> None:
        """终态定格：异常/规划失败/超时卡。"""
        self._finished = True
        card = {
            "header": "研究任务异常终止",
            "elements": [
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": (
                    f"**任务**：{self._task_short()}\n"
                    f"**原因**：{reason[:200]}\n"
                    f"**耗时**：{_fmt_elapsed(self._now() - self._t0)}"
                )}},
            ],
        }
        self._push(card)

    # === 内部 ===

    def _push(self, card_json: dict[str, Any]) -> None:
        if self._disabled or self._broken:
            return
        try:
            self.im.update_card(self._msg_id, card_json)
            self._last_push = self._now()
        except Exception:  # noqa: BLE001 —— 更新失败熔断
            logger.exception("research progress card update failed (broken)")
            self._broken = True

    def _task_short(self) -> str:
        t = self.task_text
        return t[:_TASK_PREVIEW_CAP] + ("…" if len(t) > _TASK_PREVIEW_CAP else "")

    def _render_planning(self) -> dict[str, Any]:
        return {
            "header": "研究任务执行中",
            "elements": [
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": (
                    f"**任务**：{self._task_short()}\n"
                    "**阶段**：规划中（LLM 生成执行计划）…\n"
                    f"**已用时**：{_fmt_elapsed(self._now() - self._t0)}"
                )}},
            ],
        }

    def _render_running(self, snap: dict[str, Any]) -> dict[str, Any]:
        c = snap["counts"]
        terminal = (c.get("success", 0) + c.get("failed", 0)
                    + c.get("skipped", 0) + c.get("denied", 0))
        total = snap.get("total") or self._total
        lines = [
            f"**任务**：{self._task_short()}",
            f"**阶段**：执行中 · 节点 {terminal}/{total}"
            f"（✓{c.get('success', 0)} ✗{c.get('failed', 0)}"
            f" ⊘{c.get('denied', 0)} –{c.get('skipped', 0)}）",
        ]
        if snap["running"]:
            lines.append("**运行中**：")
            lines.extend(
                f"- {label}（{_fmt_elapsed(el)}）"
                for label, el in snap["running"][:4])
        if snap["done"]:
            lines.append("**最近完成**：")
            lines.extend(
                f"- {mark} {label}" for mark, label in snap["done"][-_RECENT_KEEP:])
        lines.append(f"**已用时**：{_fmt_elapsed(self._now() - self._t0)}")
        return {
            "header": "研究任务执行中",
            "elements": [
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": "\n".join(lines)}},
            ],
        }


class ResearchRunner:
    """研究任务后台执行器：受理即回，完成后回结果。"""

    def __init__(self, *, orchestrator: Any,
                 session_factory: Callable[[], Session],
                 im_adapter: IMAdapter | None = None) -> None:
        self.orch = orchestrator
        self.session_factory = session_factory  # () -> SQLAlchemy Session（独立）
        self.im = im_adapter or orchestrator.im
        settings = getattr(orchestrator, "settings", None)
        self.timeout_sec = getattr(
            settings, "research_task_timeout_sec", 300
        ) if settings is not None else 300
        # Phase 14：写回审批模式（card_confirm / bind_scope）与审批超时
        self.writeback_mode = getattr(
            settings, "research_writeback_approval", "bind_scope"
        ) if settings is not None else "bind_scope"
        self.approval_timeout_sec = getattr(
            settings, "research_approval_timeout_sec", 600
        ) if settings is not None else 600

    # === 对外入口 ===

    def handle(self, incoming: IncomingMessage) -> dict[str, Any]:
        """process() 的 /research 分支入口：解析参数并受理/提示用法。

        有任务描述 → 受理即回 + 后台线程执行；
        无参数 → 回用法提示。
        """
        task_text = incoming.text.strip()[len("/research"):].strip()
        if not task_text:
            self.im.reply(incoming.chat_id, _USAGE_MSG)
            return {"status": "research_usage"}
        if not hasattr(self.orch, "planner"):
            self.im.reply(
                incoming.chat_id,
                "[错误] 研究引擎未初始化（缺少 settings/doc_adapter 配置）",
            )
            return {"status": "research_engine_unavailable"}

        self.im.reply(incoming.chat_id, _ACCEPT_MSG)
        # Phase 41：受理即建进度卡（「规划中」卡面）；发卡失败/无 message_id
        # 自动禁用，任务走旧版纯文本路径
        card = _ResearchProgressCard(self.im, incoming.chat_id, task_text)
        card.start()
        t = threading.Thread(
            target=self._run, args=(incoming, task_text, card), daemon=True
        )
        t.start()
        return {"status": "research_accepted", "task_text": task_text}

    # === 后台执行 ===

    def _run(self, incoming: IncomingMessage, task_text: str,
             card: "_ResearchProgressCard | None" = None) -> None:
        """后台线程主体：独立 session 落库，plan→schedule→render→写文档→回复。"""
        card = card or _ResearchProgressCard(self.im, incoming.chat_id, task_text)
        session = self.session_factory()
        try:
            out = self._execute(incoming, task_text, session, card)
            session.commit()
            logger.info("research task done: %s", out.get("status"))
        except Exception:
            logger.exception("research task failed")
            card.finish_error("执行异常（详见服务端日志）")
            try:
                session.rollback()
            except Exception:
                logger.warning("research session rollback failed")
            try:
                self.im.reply(incoming.chat_id, "[错误] 研究任务执行异常，请查看日志")
            except Exception:
                pass
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _execute(self, incoming: IncomingMessage, task_text: str,
                 session: Session,
                 card: "_ResearchProgressCard | None" = None
                 ) -> dict[str, Any]:
        """主链路：session/task 落库 → plan → schedule → 渲染 → 写文档 → 回复。"""
        # Phase 41：card 缺省时给禁用卡（下游调用点零判断）
        if card is None:
            card = _ResearchProgressCard(self.im, incoming.chat_id, task_text)
        from orchestrator.session_service import SessionService
        from orchestrator.task_service import TaskService
        from persistence.repositories.audit_repo import AuditRepo
        from persistence.repositories.session_repo import SessionRepo
        from persistence.repositories.task_repo import TaskRepo

        session_repo = SessionRepo(session)
        session_service = SessionService(session_repo)
        task_service = TaskService(TaskRepo(session), AuditRepo(session))

        session_id = session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = task_service.create(
            session_id=session_id,
            message_id=incoming.message_id,
            intent="research",
        )

        # 1. 规划（planner/executor/template 无 DB 状态，可跨线程复用）
        # 绑定文档等运行时上下文注入 prompt——模型无从得知 doc_id，
        # 不注入则 read_doc 只能编占位符（真机 2026-08-30 发现）
        bound_doc_pre = session_service.bound_doc_id(session_id)
        # Phase 17：节点级 L2 审批（write_doc 开放规划 + 执行前卡片确认）
        broker = getattr(self.orch, "approval_broker", None)
        allow_node_l2 = (
            getattr(self.orch.settings, "research_allow_node_l2", True)
            and broker is not None
        )
        if bound_doc_pre:
            session_context = (
                f"当前会话已绑定文档 doc_id={bound_doc_pre}"
                f"（read_doc 的 doc_id 直接用它）"
            )
            if allow_node_l2:
                session_context += (
                    "。如需把结果写入绑定文档，可规划 write_doc 工具节点"
                    f"（doc_id 用 {bound_doc_pre}，blocks 为文本块列表，"
                    "每块形如 {\"type\": \"text\", \"text\": \"...\"}）；"
                    "该节点执行前会发卡片请求用户确认。"
                )
        else:
            # 区分「从未绑定」与「绑定过期」：过期时模型只看到"未绑定"
            # 会尝试编造占位符（真机 2026-08-30 docx invalid param）
            stale = session_repo.get(session_id)
            if stale is not None and stale.bound_doc_id:
                session_context = (
                    f"当前会话的文档绑定已过期（原文档 doc_id="
                    f"{stale.bound_doc_id}）。read_doc 等文档工具调用会被"
                    "拒绝；严禁编造 doc_id，应在回复中提示用户先执行 "
                    "/bind-doc 重新绑定。"
                )
                self.im.reply(
                    incoming.chat_id,
                    "[提示] 文档绑定已过期，本次涉文档的读取/写回将受限；"
                    "可先 /bind-doc 重新绑定后重试。",
                )
            else:
                session_context = (
                    "当前会话未绑定文档（涉及文档读取时应在回复中提示"
                    "用户先 /bind-doc）"
                )
        # Phase 42：数据画像注入——任务文本含 dataset_ref 时附真实统计
        # （n_cells/genes_per_cell median 等），模型按数据实况选 sc_qc
        # 阈值，根治 SC_QC_OVERFILTERED（真机 2026-09-07 默认 600 全过滤）
        try:
            from orchestrator.tools.bio.dataset_profile import (
                build_profile_context,
            )
            profile_ctx = build_profile_context(
                task_text,
                getattr(self.orch.settings, "bio_workspace_root", ""))
            if profile_ctx:
                session_context += "\n" + profile_ctx
        except Exception:  # noqa: BLE001 —— 画像是可选项，绝不影响规划
            logger.warning("dataset profile inject failed", exc_info=True)
        # L2 副作用工具默认不可规划（名字与 schema 都不给模型）；
        # Phase 17 例外：开关开启且有 broker 时放行 write_doc（节点级审批，
        # 其余 L2——send_card/write_base_projection/upload_drive——仍不给）；
        # Phase 16 ACL：禁用名单内工具同样不给模型
        disabled = parse_disabled_tools(
            getattr(self.orch.settings, "disabled_tools", ""))
        visible = [
            t for t in self.orch.registry.list(planner_visible=True)
            if t.name not in disabled
            and (t.risk_level != "L2_side_effect"
                 or (allow_node_l2 and t.name == "write_doc"))
        ]
        available_tools = [t.name for t in visible]
        tools_schema = [t.to_openai_function() for t in visible]
        try:
            plan = self.orch.planner.plan(
                message=task_text,
                session_id=session_id,
                task_id=task_id,
                available_tools=available_tools,
                tools_schema=tools_schema,
                session_context=session_context,
            )
        except Exception as e:
            task_service.mark_failed(
                task_id=task_id, error_code="PLAN_FAILED", error_message=str(e)
            )
            card.finish_error(f"规划失败：{e}")
            self.im.reply(
                incoming.chat_id,
                f"[错误] 研究任务规划失败：{e}\n"
                "可尝试把任务描述写得更明确后重试。",
            )
            return {"status": "plan_failed", "task_id": task_id, "error": str(e)}

        # plan 落日志：真机排障需要看到模型到底规划了什么（输出字段引用
        # 是否正确只能靠它判断，2026-08-30）
        logger.info("research plan: %s", plan.model_dump_json())
        card.plan_done(plan)  # Phase 41：进度卡转「执行中」

        # 2. 执行（整体 wall-clock 超时保护）
        # T3：condition_llm 注入——branch/while 条件判定器（orch.llm 即 LLMRouter）
        # 自愈轮：code_repair_llm 注入——run_python 代码级失败 LLM 修复重跑
        # Phase 17：l2_gate 注入——write_doc 节点执行前卡片审批
        node_write_map: dict[str, str] = {}  # node_id -> doc_write_id（收尾补状态机）
        l2_gate = None
        if allow_node_l2:
            l2_gate = self._make_l2_gate(
                session=session, session_id=session_id, task_id=task_id,
                # allow_node_l2 已含 broker is not None 判定，此处收窄仅为类型
                incoming=incoming, broker=cast("ApprovalBroker", broker),
                bound_doc=bound_doc_pre,
                task_text=task_text, node_write_map=node_write_map,
            )
        scheduler = Scheduler(
            plan=plan, executor=self.orch.executor,
            max_concurrent=self.orch.settings.max_concurrent_nodes,
            condition_llm=_research_llm(self.orch),
            code_repair_llm=_research_llm(self.orch),
            node_repair_max_retries=getattr(
                self.orch.settings, "node_repair_max_retries", 1),
            l2_gate=l2_gate,
        )
        loop = asyncio.new_event_loop()
        # Phase 41：节点状态观察线程（每 2s 快照喂进度卡；卡内部节流/熔断）
        watch_stop = threading.Event()
        watcher = threading.Thread(
            target=self._watch_nodes, args=(scheduler, card, watch_stop),
            daemon=True)
        watcher.start()
        # Phase 20/21：plan 含 sc_*/st_* 节点时放宽 wall-clock（单细胞/
        # 空间转录组分析单节点可达数百秒；纯检索任务维持原超时不受影响）
        wall_timeout = self.timeout_sec
        if any(n.kind == "tool"
               and (n.tool_name or "").startswith(("sc_", "st_"))
               for n in plan.nodes):
            wall_timeout = max(
                self.timeout_sec,
                getattr(self.orch.settings, "research_sc_timeout_sec", 3600),
            )
        try:
            result = loop.run_until_complete(
                asyncio.wait_for(
                    scheduler.run_until_done(), timeout=wall_timeout
                )
            )
        except asyncio.TimeoutError:
            task_service.mark_failed(
                task_id=task_id, error_code="RESEARCH_TIMEOUT",
                error_message=f"exceeded {wall_timeout}s",
            )
            card.finish_error(f"超过 {wall_timeout}s 超时终止")
            self.im.reply(
                incoming.chat_id,
                f"[超时] 研究任务超过 {wall_timeout}s 未完成，已终止。"
                "可尝试拆小任务后重试。",
            )
            return {"status": "research_timeout", "task_id": task_id}
        finally:
            watch_stop.set()
            watcher.join(timeout=2)
            loop.close()

        # 2.5 Phase 17：write_doc 节点审批记录收尾（按节点终态补状态机）
        self._finalize_node_writes(session, scheduler, node_write_map)

        # 3. 渲染结果（文档块 + IM 文本摘要）
        node_lines = [
            f"- {nid}: {state.value}" for nid, state in result.node_states.items()
        ]
        outputs_digest = self._outputs_digest(scheduler)
        failure_lines = self._failure_digest(scheduler)
        # 任务标识（描述摘要+时间戳）：多任务写同文档时用户可区分哪次写入
        task_label = "{} · {:%m-%d %H:%M}".format(
            task_text[:30] + ("…" if len(task_text) > 30 else ""),
            datetime.now(),
        )
        blocks = self.orch.template.render_plan_summary_blocks(
            status=result.status,
            node_states={k: v.value for k, v in result.node_states.items()},
            artifacts_count=0,
            outputs=outputs_digest,  # 关键输出也落文档（Phase 14 真机发现缺失）
            task_label=task_label,
        )

        # 4. 绑定文档则写回（Phase 14：card_confirm 卡片确认 / bind_scope 直写）
        # render_blocks 是 DocAdapter 真实 API（原 append_blocks 不存在，
        # 真机 2026-08-30 发现 AttributeError）
        bound_doc = session_service.bound_doc_id(session_id)
        doc_written = False
        writeback_note = ""
        # Phase 17：模型已规划 write_doc 节点（无论终态）→ 写回以该节点
        # 为准，收尾不再自动写回（防双写）
        has_write_node = any(
            n.kind == "tool" and n.tool_name == "write_doc" for n in plan.nodes
        )
        if has_write_node:
            write_states = [
                result.node_states.get(n.node_id)
                for n in plan.nodes
                if n.kind == "tool" and n.tool_name == "write_doc"
            ]
            ok_states = [s for s in write_states if s == ExecutionState.SUCCESS]
            if ok_states:
                doc_written = True
                writeback_note = "写回已由 write_doc 节点执行（跳过自动写回）"
            else:
                writeback_note = (
                    "write_doc 节点未成功写入（审批拒绝/执行失败），"
                    "已跳过自动写回"
                )
        elif bound_doc:
            # Phase 20：sc 分析图上传 drive 后追加 ImageBlock
            # （card_confirm 审批预览与 bind_scope 直写均可见）
            self._inject_sc_image_blocks(
                blocks, plan, scheduler, bound_doc, has_write_node)
            broker = getattr(self.orch, "approval_broker", None)
            if self.writeback_mode == "card_confirm" and broker is not None:
                doc_written, writeback_note = self._writeback_with_confirm(
                    session=session, session_id=session_id, task_id=task_id,
                    incoming=incoming, blocks=blocks, broker=broker,
                    task_text=task_text, status=result.status,
                    node_count=len(result.node_states),
                    outputs_digest=outputs_digest,
                )
            else:
                # bind_scope（或 broker 未装配降级）：绑定即授权直写
                try:
                    self.orch.doc_adapter.render_blocks(bound_doc, blocks)
                    doc_written = True
                except Exception as e:
                    logger.warning("research doc write failed: %s", e)
                    writeback_note = f"写入失败：{e}"

        # 5. IM 回复结果摘要（失败节点附 error_code/message，便于排障）
        reply_lines = [f"[研究任务] 执行完成（{result.status}）"]
        reply_lines.extend(node_lines)
        if failure_lines:
            reply_lines.append("")
            reply_lines.append("失败详情：")
            reply_lines.extend(failure_lines)
        if outputs_digest:
            reply_lines.append("")
            reply_lines.append("关键输出：")
            reply_lines.extend(outputs_digest)
        if writeback_note:
            reply_lines.append("")
            reply_lines.append(f"写回：{writeback_note}")
        elif doc_written:
            reply_lines.append("")
            reply_lines.append(f"结果已写入绑定文档 {bound_doc}")
        self.im.reply(incoming.chat_id, "\n".join(reply_lines))

        # 5.5 Phase 20：sc_* 节点产图回传（umap/dotplot/violin → IM 图片消息）
        images_sent = self._send_sc_images(incoming, plan, scheduler)

        # Phase 41：终态定格（完成卡）
        card.finish(result.status,
                    {k: v.value for k, v in result.node_states.items()})

        # 6. task 收尾
        task_service.mark_success(
            task_id=task_id, reply_text="\n".join(reply_lines[:20])
        )
        return {
            "status": result.status,
            "task_id": task_id,
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "node_states": {k: v.value for k, v in result.node_states.items()},
            "doc_written": doc_written,
            "images_sent": images_sent,
        }

    # === Phase 41：节点状态观察线程（进度卡数据源） ===

    def _watch_nodes(self, scheduler: Scheduler, card: _ResearchProgressCard,
                     stop: threading.Event) -> None:
        """每 2s 拍 scheduler 节点快照喂进度卡（卡内部节流/熔断/禁用）。

        观察失败只记日志——绝不影响研究任务本身。
        """
        while not stop.wait(2.0):
            try:
                card.tick(self._snapshot_nodes(scheduler))
            except Exception:  # noqa: BLE001
                logger.exception("research progress watch tick failed")

    @staticmethod
    def _snapshot_nodes(scheduler: Scheduler) -> dict[str, Any]:
        """scheduler 节点状态快照：分类计数 + 运行中（含已耗时）+ 完成序列。

        观察线程与单测共用；total 动态取 len(plan.nodes)（控制流展开
        会追加节点）。done 按 handles 插入序（≈提交序），卡片自取尾部。
        """
        tool_names = {
            n.node_id: (n.tool_name or n.kind) for n in scheduler.plan.nodes
        }
        counts: dict[str, int] = {}
        running: list[tuple[str, float]] = []
        done: list[tuple[str, str]] = []
        now = datetime.now(UTC)
        for nid, h in list(scheduler._handles.items()):
            label = f"{nid} {tool_names.get(nid, '')}".strip()
            state = h.state
            if state == ExecutionState.RUNNING:
                counts["running"] = counts.get("running", 0) + 1
                started = h.started_at
                el = (now - started).total_seconds() if started else 0.0
                running.append((label, max(el, 0.0)))
                continue
            key = state.value  # success/failed/skipped/denied
            counts[key] = counts.get(key, 0) + 1
            mark = {"success": "✓", "failed": "✗",
                    "denied": "⊘", "skipped": "–"}.get(key, "?")
            done.append((mark, label))
        return {
            "counts": counts, "running": running, "done": done,
            "total": len(scheduler.plan.nodes),
        }

    # === Phase 20：sc_* 分析图 IM 回传 ===

    def _sc_image_host_paths(self, plan: DAGPlan, scheduler: Scheduler,
                             ws_root: str) -> list[str]:
        """收集成功 sc_*/st_* 节点输出的图片主机路径
        （umap/dotplot/spatial/plot，有序）。

        容器内 /ws/... 路径映射回 bio_workspace_root；非法路径跳过。
        IM 发图与文档写回共用本收集逻辑。
        """
        tool_names = {
            n.node_id: n.tool_name
            for n in plan.nodes if n.kind == "tool"
        }
        paths: list[str] = []
        for node_id, handle in scheduler._handles.items():
            if handle.state != ExecutionState.SUCCESS or not handle.outputs:
                continue
            if not (tool_names.get(node_id) or "").startswith(("sc_", "st_")):
                continue
            raw: list[str] = []
            for key in ("umap_png", "dotplot_png", "spatial_png"):
                if handle.outputs.get(key):
                    raw.append(handle.outputs[key])
            raw.extend(p for p in (handle.outputs.get("pngs") or [])
                       if isinstance(p, str))
            for p in raw:
                host = self._container_to_host_path(p, ws_root)
                if host is None:
                    logger.warning("sc image path not under /ws: %s", p)
                    continue
                paths.append(host)
        return paths

    def _send_sc_images(self, incoming: IncomingMessage, plan: DAGPlan,
                        scheduler: Scheduler) -> int:
        """成功 sc_* 节点的分析图逐张上传发送到 IM。

        附属动作：单图失败只记日志，不影响研究任务本身的
        success 结论。返回成功发送张数。
        """
        ws_root = getattr(self.orch.settings, "bio_workspace_root", "")
        if not ws_root:
            return 0
        sent = 0
        for host in self._sc_image_host_paths(plan, scheduler, ws_root):
            try:
                image_key = self.im.upload_image(host)
                self.im.send_image(incoming.chat_id, image_key)
                sent += 1
            except Exception as e:
                logger.warning("sc image send failed (%s): %s", host, e)
        return sent

    def _inject_sc_image_blocks(self, blocks: list[AnyBlock], plan: DAGPlan,
                                scheduler: Scheduler,
                                bound_doc: str | None,
                                has_write_node: bool) -> None:
        """写回 blocks 尾部追加 sc 分析图 ImageBlock（path 模式，Phase 20）。

        实际三步插入（空块→drive 上传→replace_image）由 DocAdapter
        渲染时执行。仅自动写回路径注入；write_doc 节点路径不注入
        （写回内容由该节点 blocks 决定）。
        """
        if has_write_node or not bound_doc:
            return
        from orchestrator.blocks.schemas import ImageBlock

        ws_root = getattr(self.orch.settings, "bio_workspace_root", "")
        if not ws_root:
            return
        for host in self._sc_image_host_paths(plan, scheduler, ws_root):
            blocks.append(ImageBlock(path=host, alt=Path(host).stem))

    @staticmethod
    def _container_to_host_path(container_path: str, ws_root: str) -> str | None:
        """bio 容器内图片路径 → 主机路径（/ws/<rel> → ws_root/<rel>）。

        非 /ws 前缀路径返回 None（调用方记日志跳过）。
        """
        p = str(container_path).replace("\\", "/")
        if p.startswith("/ws/"):
            rel = p[len("/ws/"):]
        elif p.startswith("ws/"):
            rel = p[len("ws/"):]
        else:
            return None
        # 原生 Path 拼接：生产 ws_root 恒为 Windows 主机路径（bio 容器挂载源），
        # 测试用平台原生 tmp_path 根；盘符根的 Windows 语义单测仅 win32 跑。
        return str(Path(ws_root).resolve() / rel)

    # === Phase 17：节点级 L2 审批（write_doc 卡片确认） ===

    def _make_l2_gate(
        self, *, session: Session, session_id: str, task_id: str,
        incoming: IncomingMessage, broker: ApprovalBroker,
        bound_doc: str | None,
        task_text: str, node_write_map: dict[str, str],
    ) -> Callable[[DAGNode, dict[str, Any]], tuple[bool, str]]:
        """构造 scheduler 的 l2_gate 闭包（捕获本次任务的审批上下文）。"""
        def gate(node: DAGNode, inputs: dict[str, Any]) -> tuple[bool, str]:
            if node.tool_name != "write_doc":
                return True, ""  # 防御性放行（其余 L2 不会出现在 plan 里）
            return self._gate_write_doc(
                node=node, inputs=inputs, session=session,
                session_id=session_id, task_id=task_id,
                incoming=incoming, broker=broker, bound_doc=bound_doc,
                task_text=task_text, node_write_map=node_write_map,
            )
        return gate

    def _gate_write_doc(
        self, *, node: DAGNode, inputs: dict[str, Any], session: Session,
        session_id: str, task_id: str,
        incoming: IncomingMessage, broker: ApprovalBroker,
        bound_doc: str | None,
        task_text: str, node_write_map: dict[str, str],
    ) -> tuple[bool, str]:
        """write_doc 节点审批门：校验 doc_id → 落 pending → 发卡 → 等决策。

        返回 (approved, error_code)。approve 后 doc_write 记 approved，
        终态（success/failed）由 _finalize_node_writes 按节点结果补。
        任何异常按拒绝收场（安全侧失败）。
        """
        from orchestrator.doc_write_service import DocWriteService
        from persistence.repositories.doc_write_repo import DocWriteRepo
        from persistence.repositories.session_repo import SessionRepo
        from shared.errors import DocWriteError

        # 1. doc_id 必须 = 绑定文档（模型编造/引用错位直接拒，不发卡）
        if not bound_doc:
            return False, "TOOL_DENIED"
        if inputs.get("doc_id") != bound_doc:
            logger.warning(
                "write_doc node %s targets %r, not bound doc %r",
                node.node_id, inputs.get("doc_id"), bound_doc)
            return False, "TOOL_DENIED"

        svc = DocWriteService(
            SessionRepo(session), DocWriteRepo(session), self.orch.doc_adapter
        )
        try:
            pending = svc.create_confirm_pending(
                session_id=session_id, task_id=task_id,
                requested_by=incoming.sender_open_id,
                preview_text=self._write_doc_preview(inputs),
                approval_mode="node_l2",
            )
        except DocWriteError as e:
            logger.warning("node_l2 pending create failed: %s", e)
            return False, "TOOL_DENIED"
        doc_write_id = pending["doc_write_id"]

        try:
            self.im.send_card(incoming.chat_id, self._node_l2_card(
                node_id=node.node_id, doc_write_id=doc_write_id,
                task_text=task_text, inputs=inputs,
            ))
        except Exception as e:
            logger.warning("node_l2 approval card send failed: %s", e)
            svc.complete_confirmed(
                doc_write_id=doc_write_id, decision="deny", blocks=[])
            return False, "TOOL_DENIED"

        decision = broker.wait(doc_write_id, self.approval_timeout_sec)
        if decision == "approve":
            DocWriteRepo(session).transition(doc_write_id, "approved")
            node_write_map[node.node_id] = doc_write_id
            return True, ""
        # deny / 超时：cancelled 收尾（complete_confirmed 非 approve 即 cancelled）
        svc.complete_confirmed(
            doc_write_id=doc_write_id, decision=decision or "deny", blocks=[])
        return False, "APPROVAL_TIMEOUT" if decision is None else "TOOL_DENIED"

    def _finalize_node_writes(self, session: Session, scheduler: Scheduler,
                              node_write_map: dict[str, str]) -> None:
        """write_doc 节点终态 → doc_writes 状态机收尾（success/failed）。

        gate 批准时记录只到 approved；节点真实执行结果由本方法补齐。
        审计粒度=审批记录，异常只记日志（不影响任务结论）。
        """
        if not node_write_map:
            return
        from persistence.repositories.doc_write_repo import DocWriteRepo

        repo = DocWriteRepo(session)
        for node_id, doc_write_id in node_write_map.items():
            handle = scheduler._handles.get(node_id)
            if handle is None:
                continue
            if handle.state == ExecutionState.SUCCESS:
                repo.transition(doc_write_id, "writing")
                repo.mark_success(
                    doc_write_id,
                    anchor_block_id=str(
                        (handle.outputs or {}).get("anchor_block_id", "")
                    ) or "",
                )
            else:
                repo.mark_failed(
                    doc_write_id,
                    reason=f"node {node_id} {handle.state.value}: "
                           f"{handle.error_code or ''} {handle.error_message or ''}",
                )

    @staticmethod
    def _write_doc_preview(inputs: dict[str, Any], max_chars: int = 400) -> str:
        """write_doc 参数摘要（落库 payload 审计 + 卡片预览）。"""
        import json

        blocks = inputs.get("blocks")
        if isinstance(blocks, list):
            head = json.dumps(blocks[:2], ensure_ascii=False, default=str)
            preview = (
                f"blocks: 共 {len(blocks)} 块；前 2 块 {head}"
            )
        else:
            preview = f"blocks: {blocks!r}"
        return (f"doc_id={inputs.get('doc_id')}; "
                f"{preview}")[:max_chars]

    @staticmethod
    def _node_l2_card(
        *, node_id: str, doc_write_id: str, task_text: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 17：write_doc 节点审批卡片（任务摘要+参数预览+按钮）。"""
        task_short = task_text[:60] + ("…" if len(task_text) > 60 else "")
        preview = ResearchRunner._write_doc_preview(inputs, max_chars=200)
        return {
            "header": f"节点 {node_id} 请求写入绑定文档（write_doc）",
            "elements": [
                {"tag": "hr"},
                {"tag": "div", "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**任务**：{task_short}\n"
                        f"**参数**：{preview}"
                    ),
                }},
                {"tag": "hr"},
                {"tag": "action", "actions": [
                    {"tag": "button",
                     "text": {"tag": "plain_text", "content": "同意执行"},
                     "type": "primary",
                     "value": {"action": "node_l2_approval",
                               "doc_write_id": doc_write_id,
                               "decision": "approve"}},
                    {"tag": "button",
                     "text": {"tag": "plain_text", "content": "跳过"},
                     "value": {"action": "node_l2_approval",
                               "doc_write_id": doc_write_id,
                               "decision": "deny"}},
                ]},
            ],
        }

    def _writeback_with_confirm(
        self, *, session: Session, session_id: str, task_id: str,
        incoming: IncomingMessage, blocks: list[AnyBlock],
        broker: ApprovalBroker,
        task_text: str = "", status: str = "", node_count: int = 0,
        outputs_digest: list[str] | None = None,
    ) -> tuple[bool, str]:
        """card_confirm 写回：落 pending → 发审批卡片 → 等决策 → 收尾状态机。

        返回 (doc_written, note)。任何分支都不抛（写回是附属动作，
        不改变研究任务本身的 success 结论）。DocWriteService 用 research
        线程自己的 session 构造（跨线程不共享主 Session）。
        task_text/status/node_count/outputs_digest 供审批卡片结构化预览
        （Phase 15 T3）。
        """
        from orchestrator.doc_write_service import DocWriteService
        from persistence.repositories.doc_write_repo import DocWriteRepo
        from persistence.repositories.session_repo import SessionRepo
        from shared.errors import DocWriteError

        svc = DocWriteService(
            SessionRepo(session), DocWriteRepo(session), self.orch.doc_adapter
        )
        preview = self.orch.template.render_blocks_to_text(blocks)
        try:
            pending = svc.create_confirm_pending(
                session_id=session_id, task_id=task_id,
                requested_by=incoming.sender_open_id,
                preview_text=preview[:800],
            )
        except DocWriteError as e:
            return False, f"已跳过（绑定无效，请 /bind-doc 后重试：{e}）"
        doc_write_id = pending["doc_write_id"]

        try:
            self.im.send_card(incoming.chat_id, self._approval_card(
                task_id=task_id, doc_write_id=doc_write_id,
                task_text=task_text, status=status, node_count=node_count,
                outputs_digest=outputs_digest or [],
            ))
        except Exception as e:
            logger.warning("approval card send failed: %s", e)
            svc.complete_confirmed(
                doc_write_id=doc_write_id, decision="deny", blocks=blocks)
            return False, "已跳过（审批卡片发送失败）"

        decision = broker.wait(doc_write_id, self.approval_timeout_sec)
        if decision == "approve":
            out = svc.complete_confirmed(
                doc_write_id=doc_write_id, decision="approve", blocks=blocks)
            if out.get("status") == "success":
                return True, "已写入文档（经卡片确认）"
            return False, f"写入失败：{out.get('reason', 'unknown')}"
        if decision == "deny":
            svc.complete_confirmed(
                doc_write_id=doc_write_id, decision="deny", blocks=blocks)
            return False, "已按您的选择跳过写回"
        # 超时：安全侧失败，按拒绝收尾
        svc.complete_confirmed(
            doc_write_id=doc_write_id, decision="timeout", blocks=blocks)
        wait_text = (f"{self.approval_timeout_sec // 60} 分钟"
                     if self.approval_timeout_sec >= 60
                     else f"{self.approval_timeout_sec} 秒")
        return False, f"审批超时（{wait_text}无人处理），已跳过写回"

    @staticmethod
    def _approval_card(
        *, task_id: str, doc_write_id: str, task_text: str,
        status: str, node_count: int, outputs_digest: list[str],
    ) -> dict[str, Any]:
        """Phase 15 T3：结构化审批卡片（任务摘要+统计+关键输出+按钮）。

        数据均来自调用方 _execute 已有产物；preview_text 落库审计不变，
        卡片只做决策所需的最小信息展示（每条输出截 120，最多 5 条）。
        """
        task_short = task_text[:80] + ("…" if len(task_text) > 80 else "")
        # 关键输出（前 5 条，每条截 120；超出提示总量）
        shown = [line[:120] + ("…" if len(line) > 120 else "")
                 for line in outputs_digest[:5]]
        outputs_md = ""
        if shown:
            outputs_md = "\n**关键输出**\n" + "\n".join(
                f"- {line}" for line in shown)
            if len(outputs_digest) > 5:
                outputs_md += f"\n- … 等共 {len(outputs_digest)} 条，完整内容将写入文档"
        return {
            "header": f"研究任务完成——是否写入绑定文档？（task {task_id[:8]}）",
            "elements": [
                {"tag": "hr"},
                {"tag": "div", "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**任务**：{task_short}\n"
                        f"执行状态：{status or 'unknown'} · Nodes: {node_count}"
                        + outputs_md
                    ),
                }},
                {"tag": "hr"},
                {"tag": "action", "actions": [
                    {"tag": "button",
                     "text": {"tag": "plain_text", "content": "同意写入"},
                     "type": "primary",
                     "value": {"action": "research_writeback",
                               "doc_write_id": doc_write_id,
                               "decision": "approve"}},
                    {"tag": "button",
                     "text": {"tag": "plain_text", "content": "跳过"},
                     "value": {"action": "research_writeback",
                               "doc_write_id": doc_write_id,
                               "decision": "deny"}},
                ]},
            ],
        }

    @staticmethod
    def _failure_digest(scheduler: Scheduler, max_chars: int = 300) -> list[str]:
        """失败节点的错误摘要（IM 回复用），单条截断防刷屏。"""
        lines: list[str] = []
        for node_id, handle in scheduler._handles.items():
            if handle.state != ExecutionState.FAILED:
                continue
            err = f"{handle.error_code or 'UNKNOWN'}: {handle.error_message or ''}"
            if len(err) > max_chars:
                err = err[:max_chars] + "…"
            lines.append(f"- {node_id} {err}")
        return lines[:5]

    @staticmethod
    def _outputs_digest(scheduler: Scheduler, max_chars: int = 800) -> list[str]:
        """成功 tool 节点的输出摘要（IM 回复用），单节点截断防刷屏。

        list/dict 输出（如 blast 的 records）JSON 序列化展示——
        否则关键结果根本不出现在回复里（真机 2026-08-30）。
        多项 list 只展示「N 项 + 首条摘要」：5 条 GenBank 记录即近
        800 字，全量展示反而淹没真正的关键输出（真机 2026-08-31）。
        每节点限 4 行 + 总限 16 行：n1/n2 的概要字段曾把 n3/n4 的
        n_clusters/markers 全挤掉（真机 2026-09-01 Phase 20）；
        图片路径字段跳过——图已单独发 IM，路径文本无信息量。
        """
        import json

        lines: list[str] = []
        for node_id, handle in scheduler._handles.items():
            if handle.state != ExecutionState.SUCCESS or not handle.outputs:
                continue
            node_lines = 0
            for key, val in handle.outputs.items():
                if node_lines >= 4:
                    break
                if (key in ("ast_notices", "umap_png", "dotplot_png",
                            "pngs", "workspace")
                        or val in (None, "", [], {})):
                    continue
                if isinstance(val, list) and len(val) > 2:
                    head = json.dumps(val[0], ensure_ascii=False, default=str)
                    val = f"共 {len(val)} 项（防刷屏略）；首条: {head[:200]}"
                elif not isinstance(val, str):
                    val = json.dumps(val, ensure_ascii=False, default=str)
                text = val if len(val) <= max_chars else val[:max_chars] + "…"
                lines.append(f"[{node_id}.{key}] {text}")
                node_lines += 1
        return lines[:16]
