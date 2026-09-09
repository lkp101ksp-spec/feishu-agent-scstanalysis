"""长会话记忆编排：历史装配 / 压缩回写 / 冻结续跑 / 手动清空（2026-09-08 spec）。

设计要点：一切外部故障（DB、压缩/摘要 LLM）降级处理，绝不阻断聊天主路径。
"""
from __future__ import annotations

import logging
from typing import Literal, Optional, cast

from feishu_adapter.im_adapter import IMAdapter
from orchestrator.runtime.context_compressor import ContextCompressor
from orchestrator.session_service import SessionService
from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.message_repo import MessageRepo
from shared.errors import FreezeRequired
from shared.schemas import ChatMessage

logger = logging.getLogger(__name__)

# messages 表只会写入这三种 role；DB 读回的 str 收窄为 ChatMessage 的 Literal
_ChatRole = Literal["system", "user", "assistant"]


class ChatMemory:
    """私聊多轮记忆：prepare 装配历史（必要时压缩/冻结），append_turn 落库。"""

    def __init__(self, *, message_repo: MessageRepo, compressor: ContextCompressor,
                 session_service: SessionService, im: IMAdapter,
                 audit_repo: Optional[AuditRepo] = None) -> None:
        self.message_repo = message_repo
        self.compressor = compressor
        self.session_service = session_service
        self.im = im
        self.audit_repo = audit_repo

    def prepare(self, session_id: str, chat_id: str) -> tuple[list[ChatMessage], str, bool]:
        """读历史并做压缩/冻结编排。

        返回 (history, 生效 session_id, 是否发生冻结)；
        发生冻结时 history 为新会话的摘要种子行（可能为空）。
        """
        try:
            rows = self.message_repo.list_all(session_id)
        except Exception as e:
            # 降级：记忆失效不阻断聊天
            logger.warning("chat_memory load failed: session=%s err=%s", session_id, e)
            return [], session_id, False
        history = [ChatMessage(role=cast(_ChatRole, r.role), content=r.content)
                   for r in rows]
        try:
            compressed = self.compressor.maybe_compress(history)
        except FreezeRequired:
            return self._freeze(session_id, chat_id, history)
        except Exception as e:
            # 压缩 LLM 失败降级：本轮不压缩继续
            logger.warning("chat_memory compress failed: session=%s err=%s",
                           session_id, e)
            return history, session_id, False
        if compressed is not history:
            # 发生了压缩：回写 DB，下次 load 即压缩态
            try:
                self.message_repo.replace_all(
                    session_id, [(m.role, m.content) for m in compressed])
            except Exception as e:
                logger.warning("chat_memory writeback failed: session=%s err=%s",
                               session_id, e)
        return compressed, session_id, False

    def _freeze(self, session_id: str, chat_id: str,
                history: list[ChatMessage]) -> tuple[list[ChatMessage], str, bool]:
        """FreezeRequired 编排：摘要 → freeze_session → 新会话播摘要 → 通知。"""
        try:
            summary = self.compressor.summarize_only(history)
        except Exception as e:
            # 摘要失败降级：空摘要冻结，新会话从零开始
            logger.warning("chat_memory freeze-summary failed: session=%s err=%s",
                           session_id, e)
            summary = ""
        budget = self.compressor.token_budget
        ratio = self.compressor.estimate_tokens(history) / budget if budget else 0.0
        new_sid = self.session_service.freeze_session(
            session_id=session_id, summary=summary, trigger_ratio=ratio)
        seed: list[ChatMessage] = []
        if summary:
            text = f"[已压缩] {summary}"
            try:
                self.message_repo.append(new_sid, "system", text)
            except Exception as e:
                logger.warning("chat_memory seed failed: session=%s err=%s",
                               new_sid, e)
            seed = [ChatMessage(role="system", content=text)]
        self.im.reply(chat_id, "[系统] 上下文已满，已开启新会话（历史摘要已继承）")
        return seed, new_sid, True

    def append_turn(self, session_id: str, user_text: str, reply_text: str) -> None:
        """双写 user/assistant 两条；失败仅记日志（崩溃最多丢一轮记忆，可接受）。"""
        try:
            self.message_repo.append(session_id, "user", user_text)
            self.message_repo.append(session_id, "assistant", reply_text)
        except Exception as e:
            logger.warning("chat_memory append failed: session=%s err=%s",
                           session_id, e)

    def clear(self, session_id: str) -> str:
        """/clear：手动冻结开新会话（无摘要继承）。返回新 session_id。"""
        return self.session_service.freeze_session(
            session_id=session_id, summary="", trigger_ratio=0.0)
