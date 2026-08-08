"""DocWrite 仓储：写入状态机 pending → approved → writing → success / failed。"""
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import DocWriteRow


class DocWriteRepo:
    """对 doc_writes 表的薄封装。"""

    def __init__(self, session: Session):
        self.session = session

    def create_pending(
        self,
        doc_write_id: str,
        task_id: str,
        doc_id: str,
        requested_by: str,
        approval_mode: str,
        payload_text: str,
        approval_id: Optional[str] = None,
    ) -> DocWriteRow:
        """创建 pending 状态的写入记录。payload_text 包装到 payload_json['text']。"""
        row = DocWriteRow(
            doc_write_id=doc_write_id,
            task_id=task_id,
            doc_id=doc_id,
            requested_by=requested_by,
            approval_mode=approval_mode,
            approval_id=approval_id,
            payload_json={"text": payload_text},
            status="pending",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, doc_write_id: str) -> Optional[DocWriteRow]:
        """按主键查询。"""
        return self.session.get(DocWriteRow, doc_write_id)

    def transition(self, doc_write_id: str, to_status: str) -> None:
        """状态机过渡：approved / writing / cancelled。"""
        row = self.get(doc_write_id)
        if row is None:
            return
        row.status = to_status
        self.session.flush()

    def mark_success(self, doc_write_id: str, anchor_block_id: str) -> None:
        """写入成功：填充 anchor_block_id。"""
        row = self.get(doc_write_id)
        if row is None:
            return
        row.status = "success"
        row.anchor_block_id = anchor_block_id
        self.session.flush()

    def mark_failed(self, doc_write_id: str, reason: str) -> None:
        """写入失败：填充 fail_reason。"""
        row = self.get(doc_write_id)
        if row is None:
            return
        row.status = "failed"
        row.fail_reason = reason
        self.session.flush()