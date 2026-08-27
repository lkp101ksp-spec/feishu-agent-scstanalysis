"""Phase 7: 公共模板 + 审核流。"""
from __future__ import annotations

from shared.ulid_ import new_ulid


class PublicTemplateService:
    def __init__(
        self, *, template_repo, audit_repo,
        admin_user_ids: set[str],
    ) -> None:
        self.template_repo = template_repo
        self.audit_repo = audit_repo
        self.admin_user_ids = admin_user_ids

    def submit_for_review(
        self, *, template_id: str, actor_open_id: str,
    ) -> None:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != actor_open_id:
            raise PermissionError("not owner")
        if tpl.scope not in {"user", "chat"}:
            raise ValueError(f"template scope is {tpl.scope}, cannot submit")
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="public_pending", chat_id=None,
        )
        if self.audit_repo is not None:
            self.audit_repo.write(
                audit_id=new_ulid(),
                action="template_submit_public",
                actor_type="user", actor_id=actor_open_id,
                target_type="template", target_id=template_id,
                detail={},
            )

    def approve(
        self, *, template_id: str, actor_open_id: str,
        note: str = "",
    ) -> None:
        if actor_open_id not in self.admin_user_ids:
            raise PermissionError("not admin")
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError("not found")
        if tpl.scope != "public_pending":
            raise ValueError(f"template scope is {tpl.scope}, not pending")
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="public", chat_id=None,
        )
        if self.audit_repo is not None:
            self.audit_repo.write(
                audit_id=new_ulid(),
                action="template_approve_public",
                actor_type="admin", actor_id=actor_open_id,
                target_type="template", target_id=template_id,
                detail={"note": note},
            )

    def reject(
        self, *, template_id: str, actor_open_id: str, reason: str,
    ) -> None:
        if actor_open_id not in self.admin_user_ids:
            raise PermissionError("not admin")
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError("not found")
        if tpl.scope != "public_pending":
            raise ValueError(f"template scope is {tpl.scope}, not pending")
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="user", chat_id=None,
        )
        if self.audit_repo is not None:
            self.audit_repo.write(
                audit_id=new_ulid(),
                action="template_reject_public",
                actor_type="admin", actor_id=actor_open_id,
                target_type="template", target_id=template_id,
                detail={"reason": reason},
            )

    def list_public(self, *, limit: int = 20, offset: int = 0) -> list:
        return self.template_repo.list_by_scope(
            scope="public", limit=limit, offset=offset,
        )
