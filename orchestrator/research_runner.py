"""ResearchRunner：/research 研究任务后台执行器（Phase 12 板块⑤）。

在独立线程跑 Planner→Scheduler→渲染→写文档全链路，避免阻塞 ws 回调
线程；DB 落库走独立 session（复用 Phase 11 独立 session 收尾惯例：
成功 commit / 异常 rollback）。
"""
from __future__ import annotations

import asyncio
import logging
import threading

from orchestrator.planner.scheduler import Scheduler
from shared.executor_types import ExecutionState
from shared.schemas import IncomingMessage

logger = logging.getLogger(__name__)

_ACCEPT_MSG = (
    "[研究任务] 已受理，正在规划执行…\n"
    "（任务越具体效果越好；完成后自动回复，期间可继续聊天）"
)
_USAGE_MSG = (
    "「/research」用法：/research <任务描述>，"
    "例如：/research 总结当前绑定文档的核心内容并给出三条研究建议"
)


class ResearchRunner:
    """研究任务后台执行器：受理即回，完成后回结果。"""

    def __init__(self, *, orchestrator, session_factory, im_adapter=None) -> None:
        self.orch = orchestrator
        self.session_factory = session_factory  # () -> SQLAlchemy Session（独立）
        self.im = im_adapter or orchestrator.im
        settings = getattr(orchestrator, "settings", None)
        self.timeout_sec = getattr(
            settings, "research_task_timeout_sec", 300
        ) if settings is not None else 300

    # === 对外入口 ===

    def handle(self, incoming: IncomingMessage) -> dict:
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
        t = threading.Thread(
            target=self._run, args=(incoming, task_text), daemon=True
        )
        t.start()
        return {"status": "research_accepted", "task_text": task_text}

    # === 后台执行 ===

    def _run(self, incoming: IncomingMessage, task_text: str) -> None:
        """后台线程主体：独立 session 落库，plan→schedule→render→写文档→回复。"""
        session = self.session_factory()
        try:
            out = self._execute(incoming, task_text, session)
            session.commit()
            logger.info("research task done: %s", out.get("status"))
        except Exception:
            logger.exception("research task failed")
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

    def _execute(self, incoming: IncomingMessage, task_text: str, session) -> dict:
        """主链路：session/task 落库 → plan → schedule → 渲染 → 写文档 → 回复。"""
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
        visible = self.orch.registry.list(planner_visible=True)
        available_tools = [t.name for t in visible]
        tools_schema = [
            t.to_openai_function()
            for t in visible if t.risk_level != "L2_side_effect"
        ]
        try:
            plan = self.orch.planner.plan(
                message=task_text,
                session_id=session_id,
                task_id=task_id,
                available_tools=available_tools,
                tools_schema=tools_schema,
            )
        except Exception as e:
            task_service.mark_failed(
                task_id=task_id, error_code="PLAN_FAILED", error_message=str(e)
            )
            self.im.reply(
                incoming.chat_id,
                f"[错误] 研究任务规划失败：{e}\n"
                "可尝试把任务描述写得更明确后重试。",
            )
            return {"status": "plan_failed", "task_id": task_id, "error": str(e)}

        # 2. 执行（整体 wall-clock 超时保护）
        scheduler = Scheduler(
            plan=plan, executor=self.orch.executor,
            max_concurrent=self.orch.settings.max_concurrent_nodes,
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                asyncio.wait_for(
                    scheduler.run_until_done(), timeout=self.timeout_sec
                )
            )
        except asyncio.TimeoutError:
            task_service.mark_failed(
                task_id=task_id, error_code="RESEARCH_TIMEOUT",
                error_message=f"exceeded {self.timeout_sec}s",
            )
            self.im.reply(
                incoming.chat_id,
                f"[超时] 研究任务超过 {self.timeout_sec}s 未完成，已终止。"
                "可尝试拆小任务后重试。",
            )
            return {"status": "research_timeout", "task_id": task_id}
        finally:
            loop.close()

        # 3. 渲染结果（文档块 + IM 文本摘要）
        node_lines = [
            f"- {nid}: {state.value}" for nid, state in result.node_states.items()
        ]
        outputs_digest = self._outputs_digest(scheduler)
        blocks = self.orch.template.render_plan_summary(
            status=result.status,
            node_states={k: v.value for k, v in result.node_states.items()},
            artifacts_count=0,
        )

        # 4. 绑定文档则写回（approval skip：bind-doc 前置授权语义）
        bound_doc = session_service.bound_doc_id(session_id)
        doc_written = False
        if bound_doc:
            try:
                self.orch.doc_adapter.append_blocks(bound_doc, blocks)
                doc_written = True
            except Exception as e:
                logger.warning("research doc write failed: %s", e)

        # 5. IM 回复结果摘要
        reply_lines = [f"[研究任务] 执行完成（{result.status}）"]
        reply_lines.extend(node_lines)
        if outputs_digest:
            reply_lines.append("")
            reply_lines.append("关键输出：")
            reply_lines.extend(outputs_digest)
        if doc_written:
            reply_lines.append("")
            reply_lines.append(f"结果已写入绑定文档 {bound_doc}")
        self.im.reply(incoming.chat_id, "\n".join(reply_lines))

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
        }

    @staticmethod
    def _outputs_digest(scheduler: Scheduler, max_chars: int = 800) -> list[str]:
        """成功 tool 节点的输出摘要（IM 回复用），单节点截断防刷屏。"""
        lines: list[str] = []
        for node_id, handle in scheduler._handles.items():
            if handle.state != ExecutionState.SUCCESS or not handle.outputs:
                continue
            for key, val in handle.outputs.items():
                if key in ("ast_notices",) or not isinstance(val, str) or not val:
                    continue
                text = val if len(val) <= max_chars else val[:max_chars] + "…"
                lines.append(f"[{node_id}.{key}] {text}")
        return lines[:10]
