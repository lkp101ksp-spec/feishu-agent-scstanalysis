"""Task 服务层：创建任务 + 更新状态 + 写审计。

状态语义（对应 shared.enums.TaskStatus）：
- pending → running → (success | success_with_partial_failure | failed | cancelled)

Phase 1 简化为 process() 直接调用 mark_*；Phase 2 可拆出异步 task queue。
"""
from typing import Optional

from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.task_repo import TaskRepo
from shared.enums import TaskStatus
from shared.ulid_ import new_ulid


class TaskService:
    """对 TaskRepo + AuditRepo 的封装，每个状态变更同时写审计。"""

    def __init__(self, task_repo: TaskRepo, audit_repo: AuditRepo):
        self.task_repo = task_repo
        self.audit_repo = audit_repo

    def create(
        self,
        session_id: str,
        message_id: str,
        intent: Optional[str] = None,
        plan_json: Optional[dict] = None,
    ) -> str:
        """创建任务，返回 task_id。"""
        task_id = new_ulid()
        self.task_repo.create(
            task_id=task_id,
            session_id=session_id,
            message_id=message_id,
            intent=intent,
            plan_json=plan_json,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="create_task",
            target_type="task",
            target_id=task_id,
            detail={"session_id": session_id, "intent": intent},
        )
        return task_id

    def mark_success(self, task_id: str, reply_text: str) -> None:
        """标记任务成功完成。"""
        self.task_repo.update_status(
            task_id=task_id, status=TaskStatus.SUCCESS.value, reply_text=reply_text
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="complete_task",
            target_type="task",
            target_id=task_id,
            detail={"reply_len": len(reply_text)},
        )

    def mark_partial_failure(self, task_id: str, reply_text: str, warning: str) -> None:
        """IM 已成功，但文档写入失败 —— 整体视为部分失败。"""
        self.task_repo.update_status(
            task_id=task_id,
            status=TaskStatus.SUCCESS_WITH_PARTIAL_FAILURE.value,
            reply_text=reply_text,
            error_code="DOC_WRITE_FAILED",
            error_message=warning,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="complete_task_with_warning",
            target_type="task",
            target_id=task_id,
            detail={"warning": warning},
        )

    def mark_failed(self, task_id: str, error_code: str, error_message: str) -> None:
        """标记任务失败。"""
        self.task_repo.update_status(
            task_id=task_id,
            status=TaskStatus.FAILED.value,
            error_code=error_code,
            error_message=error_message,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="fail_task",
            target_type="task",
            target_id=task_id,
            detail={"error_code": error_code, "error_message": error_message},
        )