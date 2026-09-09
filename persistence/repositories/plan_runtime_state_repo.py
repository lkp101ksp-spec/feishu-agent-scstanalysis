"""plan_runtime_state 表的 CRUD。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from persistence.models import PlanRuntimeStateRow


class PlanRuntimeStateRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        *,
        plan_id: str,
        session_id: str | None = None,
        state_json: dict[str, Any] | None = None,
        status: str = "running",
    ) -> None:
        existing = (
            self.session.query(PlanRuntimeStateRow)
            .filter_by(plan_id=plan_id)
            .one_or_none()
        )
        if existing is None:
            row = PlanRuntimeStateRow(
                plan_id=plan_id,
                session_id=session_id,
                state_json=state_json,
                status=status,
            )
            self.session.add(row)
        else:
            if session_id is not None:
                existing.session_id = session_id
            if state_json is not None:
                existing.state_json = state_json
            existing.status = status
        self.session.commit()

    def get(self, plan_id: str) -> PlanRuntimeStateRow:
        return (
            self.session.query(PlanRuntimeStateRow)
            .filter_by(plan_id=plan_id)
            .one()
        )

    def get_or_none(self, plan_id: str) -> PlanRuntimeStateRow | None:
        return (
            self.session.query(PlanRuntimeStateRow)
            .filter_by(plan_id=plan_id)
            .one_or_none()
        )
