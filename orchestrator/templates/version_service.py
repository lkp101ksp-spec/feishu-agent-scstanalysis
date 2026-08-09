"""Phase 6: 模板版本管理（创建 / 列出 / 回滚）。"""
from __future__ import annotations

from typing import Optional

from shared.ulid_ import new_ulid


class VersionService:
    def __init__(self, version_repo, template_repo) -> None:
        self.version_repo = version_repo
        self.template_repo = template_repo

    def on_template_upsert(
        self, *,
        template_id: str,
        name: str,
        description: str,
        blocks_json: Optional[str] = None,
        steps_json: Optional[str] = None,
        created_by: str,
    ) -> None:
        """Phase 6: 每次 upsert 写新版本；硬上限 10（自动删最旧）。"""
        current = self.version_repo.count(template_id)
        if current >= 10:
            self.version_repo.delete_oldest(template_id, keep=9)
            current = 9
        self.version_repo.insert(
            version_id=new_ulid(),
            template_id=template_id,
            version_number=current + 1,
            name=name, description=description,
            blocks_json=blocks_json, steps_json=steps_json,
            created_by=created_by,
        )
        # flush 让 count() 实时
        self.version_repo.session.flush()

    def list_versions(self, template_id: str) -> list:
        return self.version_repo.list_by_template(template_id)

    def rollback(
        self, *,
        template_id: str,
        version_number: int,
        caller_open_id: str,
    ) -> None:
        """Phase 6: 回滚到指定版本（写新版本号，保留历史）。"""
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of {template_id}"
            )
        version = self.version_repo.get_by_version(template_id, version_number)
        if version is None:
            raise ValueError(
                f"version {version_number} of {template_id} not found"
            )
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=version.name,
            type_=tpl.type,
            blocks_json=version.blocks_json,
            steps_json=version.steps_json,
            description=version.description,
            scope=tpl.scope, chat_id=tpl.chat_id,
        )
        self.on_template_upsert(
            template_id=template_id,
            name=version.name, description=version.description,
            blocks_json=version.blocks_json, steps_json=version.steps_json,
            created_by=caller_open_id,
        )