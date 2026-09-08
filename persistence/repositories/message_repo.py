"""messages 表仓储：长会话记忆的消息追加 / 读取 / 压缩回写。"""
from sqlalchemy.orm import Session

from persistence.models import MessageRow
from shared.ulid_ import new_ulid


class MessageRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, session_id: str, role: str, content: str) -> MessageRow:
        """追加一条消息（flush，commit 时机由调用方决定）。"""
        row = MessageRow(message_id=new_ulid(), session_id=session_id,
                         role=role, content=content)
        self.session.add(row)
        self.session.flush()
        return row

    def list_all(self, session_id: str) -> list[MessageRow]:
        """按创建时间升序取会话全部消息（message_id ULID 单调作次序兜底）。"""
        return list(
            self.session.query(MessageRow)
            .filter_by(session_id=session_id)
            .order_by(MessageRow.created_at, MessageRow.message_id)
            .all()
        )

    def replace_all(self, session_id: str, rows: list[tuple[str, str]]) -> None:
        """压缩回写：清空会话全部消息并按 (role, content) 重建。"""
        self.session.query(MessageRow).filter_by(session_id=session_id).delete()
        for role, content in rows:
            self.session.add(MessageRow(
                message_id=new_ulid(), session_id=session_id,
                role=role, content=content))
        self.session.flush()
