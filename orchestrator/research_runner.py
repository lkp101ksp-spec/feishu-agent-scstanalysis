"""ResearchRunner：/research 研究任务后台执行器（Phase 12 板块⑤）。

在独立线程跑 Planner→Scheduler→渲染→写文档全链路，避免阻塞 ws 回调
线程；DB 落库走独立 session（复用 Phase 11 独立 session 收尾惯例：
成功 commit / 异常 rollback）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

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
        # Phase 14：写回审批模式（card_confirm / bind_scope）与审批超时
        self.writeback_mode = getattr(
            settings, "research_writeback_approval", "bind_scope"
        ) if settings is not None else "bind_scope"
        self.approval_timeout_sec = getattr(
            settings, "research_approval_timeout_sec", 600
        ) if settings is not None else 600

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
        # 绑定文档等运行时上下文注入 prompt——模型无从得知 doc_id，
        # 不注入则 read_doc 只能编占位符（真机 2026-08-30 发现）
        bound_doc_pre = session_service.bound_doc_id(session_id)
        if bound_doc_pre:
            session_context = (
                f"当前会话已绑定文档 doc_id={bound_doc_pre}"
                f"（read_doc 的 doc_id 直接用它）"
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
        # L2 副作用工具整体不可规划（名字与 schema 都不给模型）：
        # 研究结果由本 Runner 自动写回绑定文档，模型规划 write_doc 只会
        # 被 approval 拒掉（真机 2026-08-30 n3 TOOL_DENIED）
        visible = [
            t for t in self.orch.registry.list(planner_visible=True)
            if t.risk_level != "L2_side_effect"
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
            self.im.reply(
                incoming.chat_id,
                f"[错误] 研究任务规划失败：{e}\n"
                "可尝试把任务描述写得更明确后重试。",
            )
            return {"status": "plan_failed", "task_id": task_id, "error": str(e)}

        # plan 落日志：真机排障需要看到模型到底规划了什么（输出字段引用
        # 是否正确只能靠它判断，2026-08-30）
        logger.info("research plan: %s", plan.model_dump_json())

        # 2. 执行（整体 wall-clock 超时保护）
        # T3：condition_llm 注入——branch/while 条件判定器（orch.llm 即 LLMRouter）
        # 自愈轮：code_repair_llm 注入——run_python 代码级失败 LLM 修复重跑
        scheduler = Scheduler(
            plan=plan, executor=self.orch.executor,
            max_concurrent=self.orch.settings.max_concurrent_nodes,
            condition_llm=getattr(self.orch, "llm", None),
            code_repair_llm=getattr(self.orch, "llm", None),
            node_repair_max_retries=getattr(
                self.orch.settings, "node_repair_max_retries", 1),
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
        if bound_doc:
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

    def _writeback_with_confirm(
        self, *, session, session_id: str, task_id: str,
        incoming: IncomingMessage, blocks: list, broker,
        task_text: str = "", status: str = "", node_count: int = 0,
        outputs_digest: list | None = None,
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
        status: str, node_count: int, outputs_digest: list,
    ) -> dict:
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
        """
        import json

        lines: list[str] = []
        for node_id, handle in scheduler._handles.items():
            if handle.state != ExecutionState.SUCCESS or not handle.outputs:
                continue
            for key, val in handle.outputs.items():
                if key in ("ast_notices",) or val in (None, "", [], {}):
                    continue
                if isinstance(val, list) and len(val) > 2:
                    head = json.dumps(val[0], ensure_ascii=False, default=str)
                    val = f"共 {len(val)} 项（防刷屏略）；首条: {head[:200]}"
                elif not isinstance(val, str):
                    val = json.dumps(val, ensure_ascii=False, default=str)
                text = val if len(val) <= max_chars else val[:max_chars] + "…"
                lines.append(f"[{node_id}.{key}] {text}")
        return lines[:10]
