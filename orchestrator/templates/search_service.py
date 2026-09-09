"""Phase 7: 模板搜索（PG trigram + LIKE）。"""
from __future__ import annotations

from typing import Optional

from persistence.models import TemplateRow
from persistence.repositories.template_repo import TemplateRepo


class TemplateSearchService:
    def __init__(self, template_repo: TemplateRepo) -> None:
        self.template_repo = template_repo

    def search(
        self, *,
        query: str = "",
        scope: Optional[str] = None,
        owner_open_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[TemplateRow]:
        return self.template_repo.search(
            query=query, scope=scope,
            owner_open_id=owner_open_id,
            limit=limit, offset=offset,
        )
