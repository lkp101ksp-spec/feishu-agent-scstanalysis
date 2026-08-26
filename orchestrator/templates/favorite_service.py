"""Phase 8 T7: 模板收藏服务（任意 scope 模板可收藏，ADR-0022）。"""
from __future__ import annotations

from shared.ulid_ import new_ulid


class FavoriteService:
    def __init__(self, favorite_repo, template_repo) -> None:
        self.favorite_repo = favorite_repo
        self.template_repo = template_repo

    def _check_exists(self, template_id: str):
        tpl = self.template_repo.get(template_id)
        if tpl is None or getattr(tpl, "archived_at", None) is not None:
            raise ValueError(f"template {template_id} not found")
        return tpl

    def favorite(
        self, *, template_id: str, caller_open_id: str,
    ) -> None:
        """收藏模板（任意 scope；幂等）。"""
        self._check_exists(template_id)
        self.favorite_repo.add(
            favorite_id=new_ulid(),
            template_id=template_id,
            user_open_id=caller_open_id,
        )

    def unfavorite(
        self, *, template_id: str, caller_open_id: str,
    ) -> None:
        """取消收藏（幂等）。"""
        self.favorite_repo.remove(
            template_id=template_id, user_open_id=caller_open_id,
        )

    def list_favorites(self, user_open_id: str) -> list:
        """我的收藏 → 模板行列表（过滤已删除）。"""
        out = []
        for tid in self.favorite_repo.list_by_user(user_open_id):
            tpl = self.template_repo.get(tid)
            if tpl is not None and getattr(tpl, "archived_at", None) is None:
                out.append(tpl)
        return out
