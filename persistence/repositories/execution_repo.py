"""executions 表的 CRUD。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import ExecutionRow
from shared.ulid_ import new_ulid


class ExecutionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        task_id: str,
        plan_id: str,
        node_id: str,
        tool_name: Optional[str],
        risk_level: str,
        inputs_json: dict,
        tool_version: Optional[str] = None,
    ) -> str:
        eid = new_ulid()
        row = ExecutionRow(
            execution_id=eid,
            task_id=task_id,
            plan_id=plan_id,
            node_id=node_id,
            tool_name=tool_name,
            tool_version=tool_version,
            risk_level=risk_level,
            state="pending",
            inputs_json=inputs_json,
            started_at=datetime.utcnow(),
        )
        self.session.add(row)
        self.session.commit()
        return eid

    def get(self, execution_id: str) -> ExecutionRow:
        return (
            self.session.query(ExecutionRow)
            .filter_by(execution_id=execution_id)
            .one()
        )

    def get_by_task_node(self, task_id: str, node_id: str) -> Optional[ExecutionRow]:
        return (
            self.session.query(ExecutionRow)
            .filter_by(task_id=task_id, node_id=node_id)
            .one_or_none()
        )

    def finish(
        self,
        execution_id: str,
        *,
        state: str,
        outputs_json: Optional[dict] = None,
        artifacts_ids: Optional[list] = None,
        approval_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        row = self.get(execution_id)
        row.state = state
        row.outputs_json = outputs_json
        row.artifacts_ids = artifacts_ids
        row.approval_id = approval_id
        row.error_code = error_code
        row.error_message = error_message
        row.finished_at = datetime.utcnow()
        self.session.commit()

    def list_by_plan(self, plan_id: str) -> list[ExecutionRow]:
        return (
            self.session.query(ExecutionRow).filter_by(plan_id=plan_id).all()
        )

    def list_by_task(self, task_id: str) -> list[ExecutionRow]:
        return (
            self.session.query(ExecutionRow).filter_by(task_id=task_id).all()
        )