"""Phase 7: 模板搜索（PG trigram + LIKE）。"""
from __future__ import annotations

from typing import Optional


class TemplateSearchService:
    def __init__(self, template_repo) -> None:
        self.template_repo = template_repo

    def search(
        self, *,
        query: str = "",
        scope: Optional[str] = None,
        owner_open_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        return self.template_repo.search(
            query=query, scope=scope,
            owner_open_id=owner_open_id,
            limit=limit, offset=offset,
        )