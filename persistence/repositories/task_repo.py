"""Task 仓储：create / get / get_by_message_id / update_status。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TaskRow


# 终态：finished_at 应被填充
_TERMINAL_STATUSES = {"success", "success_with_partial_failure", "failed", "cancelled"}


class TaskRepo:
    """对 tasks 表的薄封装。"""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        task_id: str,
        session_id: str,
        message_id: str,
        intent: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        plan_json: Optional[dict] = None,
    ) -> TaskRow:
        """创建任务行；started_at 自动设为 now，status 默认 'pending'。"""
        row = TaskRow(
            task_id=task_id,
            session_id=session_id,
            message_id=message_id,
            intent=intent,
            parent_task_id=parent_task_id,
            plan_json=plan_json or {},
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, task_id: str) -> Optional[TaskRow]:
        """按主键查询，不存在返回 None。"""
        return self.session.get(TaskRow, task_id)

    def get_by_message_id(self, message_id: str) -> Optional[TaskRow]:
        """按飞书消息 ID 查询，用于幂等去重。"""
        return self.session.query(TaskRow).filter_by(message_id=message_id).first()

    def update_status(
        self,
        task_id: str,
        status: str,
        reply_text: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """更新任务状态。

        当 status 属于终态（success / success_with_partial_failure / failed / cancelled）
        时，自动填充 finished_at。
        """
        row = self.session.get(TaskRow, task_id)
        if row is None:
            return
        row.status = status
        if reply_text is not None:
            row.reply_text = reply_text
        if error_code is not None:
            row.error_code = error_code
        if error_message is not None:
            row.error_message = error_message
        if status in _TERMINAL_STATUSES:
            row.finished_at = datetime.now(timezone.utc)
        self.session.flush()