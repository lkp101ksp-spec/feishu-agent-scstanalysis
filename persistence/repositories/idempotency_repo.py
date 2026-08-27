"""Idempotency 仓储：webhook 重投去重。

幂等键格式：`app_id:chat_id:message_id`。
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import IdempotencyKeyRow


class IdempotencyRepo:
    """对 idempotency_keys 表的薄封装。"""

    def __init__(self, session: Session):
        self.session = session

    def try_reserve(self, key: str, task_id: Optional[str] = None) -> bool:
        """尝试保留幂等键。

        返回：
        - True：首次处理（已写入新行）
        - False：重投（key 已存在）或底层失败

        失败时（唯一约束冲突、表不存在等）回滚并返回 False。
        """
        try:
            existing = self.session.get(IdempotencyKeyRow, key)
            if existing is not None:
                return False
            row = IdempotencyKeyRow(
                key=key,
                task_id=task_id,
                processed_at=datetime.now(timezone.utc),
            )
            self.session.add(row)
            self.session.flush()
        except Exception:
            self.session.rollback()
            return False
        return True

    def link_task(self, key: str, task_id: str) -> None:
        """关联 task_id 到已存在的幂等键。"""
        row = self.session.get(IdempotencyKeyRow, key)
        if row is not None:
            row.task_id = task_id
            self.session.flush()
