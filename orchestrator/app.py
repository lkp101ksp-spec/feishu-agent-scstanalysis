"""Orchestrator 主流程：串联 Session / BindDoc / Task / LLM / IM / Doc-Write。

业务流程：
1. 收到 IncomingMessage
2. 若为 /bind-doc 指令：调用 BindDocService.bind + 回复 IM
3. 否则：创建 task → LLM 生成 reply → IM 回复 → 若有有效 bind 则写文档 → 收尾 task 状态

返回 dict 结构（status / task_id / session_id / reply_text / doc_written / doc_id / warning）。
"""
from typing import Optional

from feishu_adapter.im_adapter import IMAdapter
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.llm_router import LLMRouter
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
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
    ):
        self.llm = llm_router
        self.session_service = session_service
        self.task_service = task_service
        self.bind_doc_service = bind_doc_service
        self.doc_write_service = doc_write_service
        self.im = im_adapter

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