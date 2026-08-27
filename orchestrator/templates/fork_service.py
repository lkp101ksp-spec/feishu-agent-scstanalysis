"""Phase 7: fork public template to user-owned."""
from __future__ import annotations

from shared.ulid_ import new_ulid


class ForkService:
    def __init__(self, template_repo) -> None:
        self.template_repo = template_repo

    def fork_from_public(
        self, *, source_template_id: str, actor_open_id: str,
    ) -> str:
        src = self.template_repo.get(source_template_id)
        if src is None or src.archived_at:
            raise ValueError(f"template {source_template_id} not found")
        if src.scope != "public":
            raise PermissionError(
                f"template scope is {src.scope}, only public can fork"
            )
        new_id = new_ulid()
        self.template_repo.upsert(
            template_id=new_id,
            owner_open_id=actor_open_id,
            name=f"{src.name} (fork by {actor_open_id})",
            type_=src.type,
            blocks_json=src.blocks_json,
            steps_json=src.steps_json,
            description=src.description,
            scope="user", chat_id=None,
            lineage_template_id=source_template_id,
        )
        return new_id

    def list_forks(self, source_template_id: str) -> list:
        return self.template_repo.list_by_lineage(source_template_id)
