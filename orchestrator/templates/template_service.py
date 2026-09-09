"""Phase 5 模板服务：CRUD + 权限校验。"""
from __future__ import annotations

import json
from typing import Any, Optional, cast

from orchestrator.blocks.schemas import AnyBlock
from orchestrator.blocks.serializer import blocks_to_json
from orchestrator.templates.renderer import render_block, render_subplan
from orchestrator.templates.schemas import SubPlanTemplateStep
from persistence.models import TemplateRow
from persistence.repositories.template_repo import TemplateRepo
from shared.ulid_ import new_ulid


class TemplateService:
    def __init__(self, repo: TemplateRepo) -> None:
        self.repo = repo

    def create_block(
        self, *, owner_open_id: str, name: str,
        blocks: list[AnyBlock], description: str = "",
    ) -> str:
        tid = new_ulid()
        self.repo.upsert(
            template_id=tid, owner_open_id=owner_open_id,
            name=name, type_="block",
            blocks_json=blocks_to_json(blocks),
            steps_json=None, description=description,
        )
        return tid

    def create_subplan(
        self, *, owner_open_id: str, name: str,
        steps: list[SubPlanTemplateStep], description: str = "",
    ) -> str:
        tid = new_ulid()
        self.repo.upsert(
            template_id=tid, owner_open_id=owner_open_id,
            name=name, type_="subplan",
            blocks_json=None,
            steps_json=json.dumps([s.model_dump() for s in steps],
                                  ensure_ascii=False),
            description=description,
        )
        return tid

    def list_by_owner(self, owner_open_id: str) -> list[TemplateRow]:
        return self.repo.list_by_owner(owner_open_id)

    def get(self, template_id: str) -> Optional[TemplateRow]:
        return self.repo.get(template_id)

    def render_block(self, *, template_id: str,
                     params: dict[str, str]) -> list[Any]:
        tpl = self.repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.type != "block":
            raise ValueError(f"template {template_id} is not a block template")
        return render_block(cast(str, tpl.blocks_json), params)

    def render_subplan(self, *, template_id: str,
                       params: dict[str, str]) -> list[SubPlanTemplateStep]:
        tpl = self.repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.type != "subplan":
            raise ValueError(f"template {template_id} is not a subplan template")
        steps_data = json.loads(tpl.steps_json or "[]")
        steps = [SubPlanTemplateStep(**s) for s in steps_data]
        return render_subplan(steps, params=params)

    def delete(self, *, template_id: str, caller_open_id: str) -> None:
        tpl = self.repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of template {template_id}"
            )
        self.repo.delete(template_id)
