from unittest.mock import MagicMock

import pytest

from orchestrator.templates.version_service import VersionService


def _make_service():
    version_repo = MagicMock()
    version_repo.count.return_value = 0
    template_repo = MagicMock()
    template_repo.get.return_value = MagicMock(
        template_id="t1", owner_open_id="ou_1", name="x",
        description="", scope="user", chat_id=None,
        archived_at=None, blocks_json="[]", steps_json=None, type="block",
    )
    return VersionService(version_repo=version_repo,
                          template_repo=template_repo), version_repo, template_repo


def test_on_template_upsert_creates_version():
    svc, version_repo, _ = _make_service()
    version_repo.count.return_value = 0
    svc.on_template_upsert(
        template_id="t1", name="x", description="d",
        blocks_json="[]", steps_json=None, created_by="ou_1",
    )
    version_repo.insert.assert_called_once()
    kwargs = version_repo.insert.call_args.kwargs
    assert kwargs["version_number"] == 1


def test_on_template_upsert_increments_version():
    svc, version_repo, _ = _make_service()
    version_repo.count.return_value = 3
    svc.on_template_upsert(
        template_id="t1", name="x", description="d",
        blocks_json="[]", steps_json=None, created_by="ou_1",
    )
    kwargs = version_repo.insert.call_args.kwargs
    assert kwargs["version_number"] == 4


def test_list_versions():
    svc, version_repo, _ = _make_service()
    version_repo.list_by_template.return_value = ["v1", "v2"]
    out = svc.list_versions("t1")
    assert out == ["v1", "v2"]


def test_rollback_requires_owner():
    svc, version_repo, template_repo = _make_service()
    template_repo.get.return_value = MagicMock(owner_open_id="ou_2")
    version_repo.get_by_version.return_value = MagicMock(
        template_id="t1", version_number=2, name="v2",
        description="d", blocks_json="[]", steps_json=None,
    )
    with pytest.raises(PermissionError):
        svc.rollback(template_id="t1", version_number=2,
                     caller_open_id="ou_1")


def test_rollback_enforces_hard_limit():
    """Phase 6: 写入 11 个版本应自动清理到 10。"""
    svc, version_repo, template_repo = _make_service()
    version_repo.count.return_value = 10
    svc.on_template_upsert(
        template_id="t1", name="x", description="d",
        blocks_json=None, steps_json=None, created_by="ou_1",
    )
    # 应触发 delete_oldest(keep=9) 以保证新增后总数 = 10
    version_repo.delete_oldest.assert_called_once_with("t1", keep=9)