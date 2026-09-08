"""DocWrite 仓储：写入状态机 pending → approved → writing → success / failed。"""
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import DocWriteRow, TaskRow


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
        anchor_text: Optional[str] = None,
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
            anchor_text=anchor_text,
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

    def mark_success(self, doc_write_id: str, anchor_block_id: Optional[str]) -> None:
        """写入成功：填充 anchor_block_id（CLI 渲染路径合法为 None，列 nullable）。"""
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

    def latest_success_anchor_for_session(
        self, session_id: str, anchor_text: Optional[str] = None
    ) -> Optional[str]:
        """该 session 最近一次成功写入的 anchor_block_id（锚点续写定位用）。

        anchor_text 非 None 时只跟随**同锚点**的上次写入（不同锚点互不串位）；
        None 时不过滤（兼容旧数据）。
        """
        query = (
            self.session.query(DocWriteRow.anchor_block_id)
            .join(TaskRow, DocWriteRow.task_id == TaskRow.task_id)
            .filter(TaskRow.session_id == session_id,
                    DocWriteRow.status == "success",
                    DocWriteRow.anchor_block_id.isnot(None),
                    DocWriteRow.anchor_block_id != "")
        )
        if anchor_text is not None:
            query = query.filter(DocWriteRow.anchor_text == anchor_text)
        row = query.order_by(DocWriteRow.created_at.desc()).first()
        return row[0] if row else None
