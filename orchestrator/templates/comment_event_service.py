"""Phase 11: 评论事件处理服务（防循环 → 绑定过滤 → sync + notify，ADR-0033）。

事件回调运行在 SDK ws 线程：本服务必须绑定独立 DB Session 组装（runtime 负责），
严禁与主管线共享 Session。sync/notify 复用 Phase 8/9 幂等服务，
与轮询通道并发触发同一 doc 不会重复落库或重复推送。

Phase 18 追加：/ask 评论问答——评论正文以 "/ask " 开头（或含 "@agent"）时，
基于绑定文档上下文 LLM 作答并 reply_comment 回写（回执幂等复用
processed_at 打标，事件重发不重复作答）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 问答上下文截断上限（字符）：超长文档截尾防 prompt 爆炸
_QA_CONTEXT_MAX_CHARS = 8000

# 问答回执前缀
_QA_PREFIX = "/ask"
_QA_MENTION = "@agent"


def extract_question(text: str) -> str | None:
    """从评论正文提取问题文本；非问答评论返回 None。

    命中 "/ask 问题"（前缀）或含 "@agent"（mention 文本化兜底）；
    提取后去掉触发词返回剩余文本。
    """
    stripped = (text or "").strip()
    if stripped.startswith(_QA_PREFIX + " "):
        q = stripped[len(_QA_PREFIX):].strip()
        return q or None
    if _QA_MENTION in stripped:
        q = stripped.replace(_QA_MENTION, "").strip()
        return q or None
    return None


def _block_tree_text(blocks: list) -> str:
    """官方块树 → 纯文本（text_run.content 拼接，块间换行）。"""
    parts: list[str] = []
    for b in blocks or []:
        text = (b.get("text") if isinstance(b, dict) else None) or {}
        for el in text.get("elements") or []:
            run = (el.get("text_run") if isinstance(el, dict) else None) or {}
            content = run.get("content")
            if content:
                parts.append(content)
        parts.append("\n")
    return "".join(parts)


class CommentEventService:
    """处理 drive.notice.comment_add_v1 事件的业务链。"""

    def __init__(self, *, session_repo, sync_service, notify_service,
                 bot_open_id: str | None, session=None,
                 comment_repo=None, doc_adapter=None, llm=None,
                 qa_reply_client=None) -> None:
        self.session_repo = session_repo
        self.sync_service = sync_service
        self.notify_service = notify_service
        self.bot_open_id = bot_open_id
        # 独立 event_session：业务完成后由本服务负责 commit（repo 只 flush）
        self.session = session
        # Phase 18 问答依赖（均可选：None 时禁用问答，不回归现行为）
        self.comment_repo = comment_repo
        self.doc_adapter = doc_adapter
        self.llm = llm
        self.qa_reply_client = qa_reply_client

    def handle(self, *, file_token: str, operator_open_id: str,
               comment_id: str = "") -> dict:
        """处理一条评论事件；异常吃掉返回 error（保长连接）。"""
        try:
            return self._handle(file_token=file_token,
                                operator_open_id=operator_open_id,
                                comment_id=comment_id)
        except Exception:
            logger.exception("comment event handling failed: %s", file_token)
            if self.session is not None:
                try:
                    self.session.rollback()
                except Exception:
                    logger.warning("event session rollback failed")
            return {"status": "error", "file_token": file_token}

    def _handle(self, *, file_token: str, operator_open_id: str,
                comment_id: str = "") -> dict:
        # 1. 防循环：bot 自身评论（回执写回触发）直接忽略；
        #    拿不到 bot id 时保守跳过（宁可漏处理不冒死循环风险）
        if self.bot_open_id is None:
            return {"status": "skipped_no_bot_id"}
        if operator_open_id == self.bot_open_id:
            return {"status": "ignored_bot_self", "file_token": file_token}

        # 2. 绑定过滤：找该 doc 的活跃绑定 session（未过期）
        now = datetime.now(timezone.utc)
        hit = None
        for s in self.session_repo.list_active():
            if s.bound_doc_id != file_token:
                continue
            exp = s.bind_expires_at
            if exp is None:
                hit = s
                break
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now:
                hit = s
                break
        if hit is None:
            return {"status": "ignored_unbound", "file_token": file_token}

        # 3. 触发同步 + 通知（幂等，与轮询通道共用）
        self.sync_service.sync(doc_id=file_token)
        out = self.notify_service.notify_new_pending(
            doc_id=file_token, owner_open_id=hit.owner_open_id,
            chat_id=hit.source_chat_id)

        # 4. Phase 18：/ask 问答（sync 落库后本地可查评论正文）
        qa = self._maybe_answer_comment(doc_id=file_token,
                                        comment_id=comment_id)

        if self.session is not None:
            self.session.commit()
        result = {"status": "handled", "file_token": file_token, **out}
        if qa:
            result["qa"] = qa
        return result

    def _maybe_answer_comment(self, *, doc_id: str, comment_id: str) -> dict | None:
        """/ask 问答回执；依赖缺失/非问答/已答过返回 None 不阻断主链。"""
        if not comment_id or self.comment_repo is None:
            return None
        if (self.doc_adapter is None or self.llm is None
                or self.qa_reply_client is None):
            return None  # 问答未装配（依赖不全），静默跳过
        comment = self.comment_repo.get(comment_id)
        if comment is None:
            return {"status": "qa_comment_not_found"}
        if comment.processed_at is not None:
            return {"status": "qa_already_answered"}  # 事件重发幂等
        question = extract_question(comment.text)
        if question is None:
            return None  # 普通评论，无问答

        # 组装文档上下文（截断保护）并作答
        blocks = self.doc_adapter.get_block_tree(doc_id)
        context = _block_tree_text(blocks)
        if len(context) > _QA_CONTEXT_MAX_CHARS:
            context = context[:_QA_CONTEXT_MAX_CHARS] + "（文档过长，已截断）"
        prompt = (
            "你是文档助手。以下是一篇飞书文档的内容，请根据文档回答评论中的问题，"
            "直接给出简洁准确的中文回答（不要复述问题）。\n\n"
            f"【文档内容】\n{context}\n\n"
            f"【评论问题】\n{question}"
        )
        answer = self.llm.call(role="comment_qa", prompt=prompt)
        self.qa_reply_client.reply_comment(
            file_token=doc_id, comment_id=comment_id, text=answer)
        # 回执成功后打标：事件重发/轮询并发触发不重复作答
        self.comment_repo.mark_processed(comment_id)
        logger.info("comment qa answered: doc=%s comment=%s",
                    doc_id, comment_id)
        return {"status": "qa_answered", "comment_id": comment_id}
