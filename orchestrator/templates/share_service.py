"""Phase 6: 群聊共享模板。"""
from __future__ import annotations

from typing import Optional


class ShareService:
    def __init__(self, template_repo) -> None:
        self.template_repo = template_repo

    def share_to_chat(
        self, *, template_id: str, chat_id: str, caller_open_id: str,
    ) -> None:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of {template_id}"
            )
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="chat", chat_id=chat_id,
        )

    def list_for_chat(self, chat_id: str) -> list:
        return self.template_repo.list_by_chat(chat_id)

    def can_access(
        self, *,
        template_id: str,
        caller_open_id: str,
        chat_id: Optional[str] = None,
    ) -> bool:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            return False
        if tpl.scope == "user":
            return tpl.owner_open_id == caller_open_id
        if tpl.scope == "chat":
            return tpl.chat_id == chat_id
        return False
