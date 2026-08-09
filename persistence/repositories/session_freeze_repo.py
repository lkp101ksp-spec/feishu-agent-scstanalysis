"""session_freezes 表的 CRUD。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from persistence.models import SessionFreezeRow
from shared.ulid_ import new_ulid


class SessionFreezeRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        origin_session_id: str,
        new_session_id: str,
        summary_id: str | None = None,
        trigger_ratio: float,
    ) -> str:
        fid = new_ulid()
        row = SessionFreezeRow(
            freeze_id=fid,
            origin_session_id=origin_session_id,
            new_session_id=new_session_id,
            summary_id=summary_id,
            trigger_ratio=trigger_ratio,
        )
        self.session.add(row)
        self.session.commit()
        return fid

    def get(self, freeze_id: str) -> SessionFreezeRow:
        return (
            self.session.query(SessionFreezeRow)
            .filter_by(freeze_id=freeze_id)
            .one()
        )

    def list_by_origin(self, origin_session_id: str) -> list[SessionFreezeRow]:
        return (
            self.session.query(SessionFreezeRow)
            .filter_by(origin_session_id=origin_session_id)
            .all()
        )