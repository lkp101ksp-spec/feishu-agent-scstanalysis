"""LLM active 仓储：单行表读写（Phase 30 模型切换持久化）。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from persistence.models import LLMActiveRow


class LLMActiveRepo:
    """对 llm_active 单行表的薄封装（id 恒为 1，upsert 语义）。"""

    def __init__(self, session: Session):
        self.session = session

    def get(self) -> LLMActiveRow | None:
        """读当前生效行（空表返回 None = 未切换过，用 yaml 默认主备）。"""
        return self.session.get(LLMActiveRow, 1)

    def upsert(self, primary_name: str, fallback_name: str) -> LLMActiveRow:
        """写入/更新当前主备候选名（切换即调用，不 commit 由调用方决定）。"""
        row = self.get()
        if row is None:
            row = LLMActiveRow(id=1, primary_name=primary_name,
                               fallback_name=fallback_name)
            self.session.add(row)
        else:
            row.primary_name = primary_name
            row.fallback_name = fallback_name
        self.session.flush()
        return row
