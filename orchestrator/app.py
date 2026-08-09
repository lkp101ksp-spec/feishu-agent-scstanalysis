"""Orchestrator 主流程：串联 Session / BindDoc / Task / LLM / IM / Doc-Write。

业务流程：
1. 收到 IncomingMessage
2. 若为 /bind-doc 指令：调用 BindDocService.bind + 回复 IM
3. 否则：创建 task → LLM 生成 reply → IM 回复 → 若有有效 bind 则写文档 → 收尾 task 状态

Phase 2 增量：保留 process() Phase 1 路径；新增 process_phase2() 走 Planner + Scheduler。
返回 dict 结构（status / task_id / session_id / reply_text / doc_written / doc_id / warning）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from feishu_adapter.im_adapter import IMAdapter
from orchestrator.approval_service import ApprovalService
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.executor.kernel_manager import KernelPool
from orchestrator.executor.local_executor import LocalExecutor
from orchestrator.executor.sandbox import DockerSandbox, DockerSandboxConfig
from orchestrator.llm_router import LLMRouter
from orchestrator.planner.planner import Planner
from orchestrator.planner.scheduler import Scheduler
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
from orchestrator.template_engine import TemplateEngine
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolRegistry
from shared.errors import BindDocInvalidError, DocWriteError, FeishuAgentError, LLMCallError
from shared.schemas import ChatMessage, IncomingMessage


SYSTEM_PROMPT = "你是飞书科研助手。请用简洁中文回答，不超过 200 字。"


class Orchestrator:
    """Phase 1 主流程编排器。"""

    def __init__(
        self,
        llm_router: LLMRouter,
        session_service: SessionService,
        task_service: TaskService,
        bind_doc_service: BindDocService,
        doc_write_service: DocWriteService,
        im_adapter: IMAdapter,
        *,
        settings=None,
        doc_adapter=None,
        base_adapter=None,
        drive_adapter=None,
        audit_repo=None,
        artifact_repo=None,
    ):
        self.llm = llm_router
        self.session_service = session_service
        self.task_service = task_service
        self.bind_doc_service = bind_doc_service
        self.doc_write_service = doc_write_service
        self.im = im_adapter

        # Phase 2 子系统（可选注入；None 时 process_phase2 不可用）
        self.settings = settings
        self.doc_adapter = doc_adapter
        self.base_adapter = base_adapter
        self.drive_adapter = drive_adapter
        self.audit_repo = audit_repo
        self.artifact_repo = artifact_repo

        if settings is not None and doc_adapter is not None and base_adapter is not None \
                and drive_adapter is not None:
            self.registry = ToolRegistry()
            from orchestrator.tools.builtin.l0_read import register_l0_read
            from orchestrator.tools.builtin.l1_compute import register_l1_compute
            from orchestrator.tools.builtin.l2_side_effect import register_l2_side_effect
            register_l0_read(
                self.registry,
                doc_adapter=doc_adapter,
                base_adapter=base_adapter,
                drive_adapter=drive_adapter,
            )
            register_l1_compute(
                self.registry, llm_router=llm_router, kernel_manager=None
            )
            register_l2_side_effect(
                self.registry,
                doc_adapter=doc_adapter,
                base_adapter=base_adapter,
                im_adapter=im_adapter,
                drive_adapter=drive_adapter,
            )
            # === Phase 4 MVP: L3 领域工具 ===
            from orchestrator.tools.builtin.l3_bio import register_l3_bio
            register_l3_bio(self.registry)
            self.approval = ApprovalService(
                im_adapter=im_adapter, approval_repo=None, audit_repo=audit_repo
            )
            self.tool_handler = ToolHandler(
                registry=self.registry, approval_service=self.approval
            )
            cfg = DockerSandboxConfig.from_settings(settings)
            self.sandbox = DockerSandbox(cfg)
            self.kernel_pool = KernelPool(
                sandbox=self.sandbox,
                idle_timeout_sec=settings.kernel_idle_timeout_sec,
            )
            self.executor = LocalExecutor(
                kernel_pool=self.kernel_pool, tool_handler=self.tool_handler
            )
            self.planner = Planner(llm_router=llm_router)
            self.template = TemplateEngine()

    def process(self, incoming: IncomingMessage) -> dict:
        """处理一条入站消息，返回结果摘要。"""
        # 1. /bind-doc 指令：单独分支
        if incoming.is_bind_doc_cmd and incoming.bind_doc_id:
            return self._handle_bind(incoming)

        # 2. 普通消息：创建 session + task
        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = self.task_service.create(
            session_id=session_id,
            message_id=incoming.message_id,
            intent="general_chat",
        )

        # 3. LLM 生成回复
        try:
            reply_text = self.llm.chat([
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=incoming.text),
            ])
        except LLMCallError as e:
            self.task_service.mark_failed(
                task_id=task_id, error_code="LLM_FAILED", error_message=str(e)
            )
            self.im.reply(incoming.chat_id, f"[错误] LLM 调用失败：{e}")
            return {
                "status": "failed",
                "task_id": task_id,
                "session_id": session_id,
                "error": str(e),
            }

        # 4. IM 回复（写文档之前先回，保证用户先看到内容）
        self.im.reply(incoming.chat_id, reply_text)

        # 5. 决定是否写文档
        bound_doc = self.session_service.bound_doc_id(session_id)
        doc_written = False
        doc_id: Optional[str] = None
        warning: Optional[str] = None
        if bound_doc:
            try:
                result = self.doc_write_service.write_plain_text(
                    session_id=session_id,
                    task_id=task_id,
                    requested_by=incoming.sender_open_id,
                    text=reply_text,
                )
                doc_written = True
                doc_id = result["doc_id"]
            except DocWriteError as e:
                warning = str(e)

        # 6. 收尾 task 状态
        if warning:
            self.task_service.mark_partial_failure(
                task_id=task_id, reply_text=reply_text, warning=warning
            )
            status = "success_with_partial_failure"
        else:
            self.task_service.mark_success(task_id=task_id, reply_text=reply_text)
            status = "success"

        return {
            "status": status,
            "task_id": task_id,
            "session_id": session_id,
            "reply_text": reply_text,
            "doc_written": doc_written,
            "doc_id": doc_id,
            "warning": warning,
        }

    def _handle_bind(self, incoming: IncomingMessage) -> dict:
        """处理 /bind-doc <doc_id> 指令。"""
        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        try:
            expires_at = self.bind_doc_service.bind(
                session_id=session_id,
                owner_open_id=incoming.sender_open_id,
                doc_id=incoming.bind_doc_id,
            )
        except BindDocInvalidError as e:
            self.im.reply(incoming.chat_id, f"[错误] bind-doc 失败：{e}")
            return {
                "status": "bind_doc_failed",
                "session_id": session_id,
                "error": str(e),
            }
        except FeishuAgentError as e:
            self.im.reply(incoming.chat_id, f"[错误] bind-doc 失败：{e}")
            return {
                "status": "bind_doc_failed",
                "session_id": session_id,
                "error": str(e),
            }

        self.im.reply(
            incoming.chat_id,
            f"[成功] 已绑定文档 {incoming.bind_doc_id}，授权有效期至 {expires_at.isoformat()}。",
        )
        return {
            "status": "bind_doc_success",
            "session_id": session_id,
            "bound_doc_id": incoming.bind_doc_id,
            "expires_at": expires_at.isoformat(),
        }

    # === Phase 2 ===

    def process_phase2(self, incoming: IncomingMessage) -> dict:
        """Phase 2 主流程：Planner → Scheduler → Template → DocWrite。

        Phase 2 简化版：sync 包装 asyncio.run_until_complete；
        Phase 2.1 引入完整 asyncio event loop。
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 2 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )

        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = self.task_service.create(
            session_id=session_id,
            message_id=incoming.message_id,
            intent="phase2_plan",
        )

        available_tools = [t.name for t in self.registry.list()]
        tools_schema = self.registry.to_openai_functions(include_L2=False)
        try:
            plan = self.planner.plan(
                message=incoming.text,
                session_id=session_id,
                task_id=task_id,
                available_tools=available_tools,
                tools_schema=tools_schema,
            )
        except Exception as e:
            self.task_service.mark_failed(
                task_id=task_id, error_code="PLAN_FAILED", error_message=str(e)
            )
            return {
                "status": "plan_failed",
                "task_id": task_id,
                "session_id": session_id,
                "error": str(e),
            }

        scheduler = Scheduler(
            plan=plan, executor=self.executor,
            max_concurrent=self.settings.max_concurrent_nodes,
        )
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(scheduler.run_until_done())
        finally:
            loop.close()

        blocks = self.template.render_plan_summary(
            status=result.status,
            node_states={k: v.value for k, v in result.node_states.items()},
            artifacts_count=0,
        )

        class _EmptySession:
            bound_doc_id = None
            bind_expires_at = None

        sess = _EmptySession()
        bound = self.session_service.bound_doc_id(session_id)
        if bound:
            sess = type(
                "BS",
                (),
                {"bound_doc_id": bound,
                 "bind_expires_at": datetime.utcnow()},
            )()
        if self.approval.policy.can_skip_approval(
            "write_doc",
            {"doc_id": getattr(sess, "bound_doc_id", None) or ""},
            sess,
        ) and getattr(sess, "bound_doc_id", None):
            self.doc_adapter.append_blocks(sess.bound_doc_id, blocks)

        self.task_service.mark_success(
            task_id=task_id, reply_text=f"Plan {result.status}"
        )
        return {
            "status": result.status,
            "task_id": task_id,
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "node_states": {k: v.value for k, v in result.node_states.items()},
        }

    # === Phase 3 ===
    def process_phase3(self, incoming: IncomingMessage) -> dict:
        """Phase 3 主流程：runtime 接管 + bind_doc 续期指令 + 上下文压缩。

        与 process_phase2 区别：
        - 新增 /bind-doc-renew 指令分支
        - Scheduler 注入 PlanRuntime（动态追加 / 循环 / 冻结 hook）
        - ContextCompressor 在 LLM 调用前监控
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 3 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )

        # 1. /bind-doc-renew 指令
        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已续期到 {new_exp.isoformat()}")
                return {
                    "status": "renew_bind",
                    "session_id": session_id,
                    "new_expires": new_exp.isoformat(),
                }
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "error": str(e)}

        # 2. 普通消息：复用 process_phase2 主路径（Phase 3 简化版）
        # 完整 dynamic-append / while / for / freeze 集成在 Phase 3.1
        return self.process_phase2(incoming)

    # === Phase 4 MVP ===
    def process_phase4(self, incoming: IncomingMessage) -> dict:
        """Phase 4 MVP：与 process_phase3 区别仅 ToolRegistry 多注册 blast_search。

        普通消息委托给 process_phase3。
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 4 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )
        # /bind-doc-renew 指令继承 Phase 3
        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已续期到 {new_exp.isoformat()}")
                return {
                    "status": "renew_bind", "session_id": session_id,
                    "new_expires": new_exp.isoformat(),
                }
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "error": str(e)}
        # 普通消息：复用 process_phase3 路径
        return self.process_phase3(incoming)

    # === Phase 5 ===
    def process_phase5(self, incoming) -> dict:
        """Phase 5 入口：复用 process_phase4 + 模板市场指令。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 5 subsystems not initialized")

        text = incoming.text.strip()
        # IM 指令路由
        if text == "/template-list":
            ts = getattr(self, "template_service", None)
            if ts is None:
                self.im.reply(incoming.chat_id, "[错误] template_service 未配置")
                return {"status": "template_list_failed"}
            templates = ts.list_by_owner(incoming.sender_open_id)
            ids = [t.template_id for t in templates]
            self.im.reply(incoming.chat_id, f"您的模板: {', '.join(ids) or '(无)'}")
            return {"status": "template_listed", "templates": ids}

        # 普通消息：复用 process_phase4
        return self.process_phase4(incoming)

    # === Phase 6 ===
    def process_phase6(self, incoming) -> dict:
        """Phase 6 入口：复用 process_phase5 + 模板版本/共享指令。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 6 subsystems not initialized")

        text = incoming.text.strip()
        if text.startswith("/template-rollback "):
            parts = text.split()
            if len(parts) < 3:
                self.im.reply(incoming.chat_id,
                              "[错误] 用法: /template-rollback <id> <version>")
                return {"status": "rollback_failed", "reason": "bad_args"}
            tid, ver = parts[1], int(parts[2])
            vs = getattr(self, "version_service", None)
            if vs is None:
                self.im.reply(incoming.chat_id, "[错误] version_service 未配置")
                return {"status": "rollback_failed"}
            try:
                vs.rollback(template_id=tid, version_number=ver,
                             caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已回滚模板 {tid} 到版本 {ver}")
                return {"status": "rolled_back", "template_id": tid,
                        "version_number": ver}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "rollback_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "rollback_failed", "reason": str(e)}

        if text.startswith("/template-share "):
            tid = text.split(maxsplit=1)[1].strip()
            ss = getattr(self, "share_service", None)
            if ss is None:
                self.im.reply(incoming.chat_id, "[错误] share_service 未配置")
                return {"status": "share_failed"}
            try:
                ss.share_to_chat(template_id=tid, chat_id=incoming.chat_id,
                                  caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 模板 {tid} 已共享到本群")
                return {"status": "shared", "template_id": tid}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "share_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "share_failed", "reason": str(e)}

        # 普通消息：复用 process_phase5
        return self.process_phase5(incoming)