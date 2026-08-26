"""Phase 8 T7: 模板标签服务（owner 权限 + 归一化，ADR-0022）。"""
from __future__ import annotations

from shared.ulid_ import new_ulid


class TagService:
    def __init__(self, tag_repo, template_repo) -> None:
        self.tag_repo = tag_repo
        self.template_repo = template_repo

    def _check_owner(self, *, template_id: str, caller_open_id: str):
        """模板存在 + 未归档 + caller 是 owner；否则抛错。"""
        tpl = self.template_repo.get(template_id)
        if tpl is None or getattr(tpl, "archived_at", None) is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of {template_id}"
            )
        return tpl

    def attach(
        self, *, template_id: str, tag: str, caller_open_id: str,
    ) -> str:
        """打标签（归一化 strip+lower）；重复静默幂等。"""
        self._check_owner(template_id=template_id, caller_open_id=caller_open_id)
        normalized = tag.strip().lower()
        if not normalized:
            raise ValueError("tag is empty")
        row = self.tag_repo.add(
            tag_id=new_ulid(),
            template_id=template_id,
            tag=normalized,
            created_by=caller_open_id,
        )
        return row.tag

    def detach(
        self, *, template_id: str, tag: str, caller_open_id: str,
    ) -> None:
        """摘标签（同 owner 权限）；不存在静默。"""
        self._check_owner(template_id=template_id, caller_open_id=caller_open_id)
        self.tag_repo.remove(
            template_id=template_id, tag=tag.strip().lower(),
        )

    def list_tags(self, template_id: str) -> list[str]:
        """列模板标签（任意人可读）。"""
        return self.tag_repo.list_by_template(template_id)

    def find_by_tag(self, tag: str) -> list:
        """按标签查未归档模板行。"""
        normalized = tag.strip().lower()
        out = []
        for tid in self.tag_repo.find_template_ids_by_tag(normalized):
            tpl = self.template_repo.get(tid)
            if tpl is not None and getattr(tpl, "archived_at", None) is None:
                out.append(tpl)
        return out
