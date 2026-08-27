"""文档写入服务层。

业务流程：
1. 校验 session 当前是否对该 doc_id 有有效 bind-doc 授权
2. DocWriteRepo.create_pending → DocWriteRepo.transition(approved) → transition(writing)
3. 调用 DocAdapter.append_plain_text 真正写文档
4. DocWriteRepo.mark_success（带 anchor_block_id）

异常路径：
- 无有效 bind：抛 DocWriteError，不创建 doc_writes
- 适配层异常：mark_failed 后抛 DocWriteError
"""
import logging
from datetime import datetime, timezone

from feishu_adapter.doc_adapter import DocAdapter
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from shared.errors import DocWriteError
from shared.ulid_ import new_ulid

logger = logging.getLogger(__name__)


class DocWriteService:
    """在 bind-doc 授权窗口内对飞书文档追加纯文本。"""

    def __init__(
        self,
        session_repo: SessionRepo,
        doc_repo: DocWriteRepo,
        doc_adapter: DocAdapter,
    ):
        self.session_repo = session_repo
        self.doc_repo = doc_repo
        self.doc_adapter = doc_adapter

    def write_plain_text(
        self, session_id: str, task_id: str, requested_by: str, text: str
    ) -> dict:
        """在授权窗口内追加纯文本。返回 {doc_write_id, doc_id, anchor_block_id, status}。

        异常：DocWriteError（无 bind / bind 过期 / 写入失败）
        """
        session_row = self.session_repo.get(session_id)
        if session_row is None or session_row.bound_doc_id is None:
            raise DocWriteError("no valid bind-doc on this session")
        if session_row.bind_expires_at is None:
            raise DocWriteError("bind-doc expired, please /bind-doc again")
        expires_at = session_row.bind_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise DocWriteError("bind-doc expired, please /bind-doc again")

        doc_id = session_row.bound_doc_id
        doc_write_id = new_ulid()

        # 1. pending → approved → writing
        self.doc_repo.create_pending(
            doc_write_id=doc_write_id,
            task_id=task_id,
            doc_id=doc_id,
            requested_by=requested_by,
            approval_mode="bind_scope",
            payload_text=text,
        )
        self.doc_repo.transition(doc_write_id, "approved")
        self.doc_repo.transition(doc_write_id, "writing")

        # 2. 真正写入
        try:
            anchor = self.doc_adapter.append_plain_text(doc_id=doc_id, text=text)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            self.doc_repo.mark_failed(doc_write_id, reason=reason)
            logger.warning("doc_write_failed doc_write_id=%s reason=%s", doc_write_id, reason)
            raise DocWriteError(f"doc write failed: {e}") from e

        # 3. success
        self.doc_repo.mark_success(doc_write_id, anchor_block_id=anchor)

        return {
            "doc_write_id": doc_write_id,
            "doc_id": doc_id,
            "anchor_block_id": anchor,
            "status": "success",
        }
