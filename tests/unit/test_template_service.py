from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from orchestrator.templates.schemas import SubPlanTemplateStep
from orchestrator.templates.template_service import TemplateService


def _make_repo():
    """创建默认 get/list 返回 None/[] 的 MagicMock。"""
    repo = MagicMock()
    repo.get.return_value = None
    repo.list_by_owner.return_value = []
    return repo


def _make_service():
    repo = _make_repo()
    return TemplateService(repo=repo), repo


def test_create_block_returns_template_id():
    svc, repo = _make_service()
    from orchestrator.blocks.schemas import HeadingBlock, TextBlock
    tid = svc.create_block(
        owner_open_id="ou_1", name="std",
        blocks=[HeadingBlock(level=2, text="Hi"), TextBlock(text="x")],
        description="x",
    )
    assert tid
    repo.upsert.assert_called_once()


def test_create_subplan_returns_template_id():
    svc, repo = _make_service()
    tid = svc.create_subplan(
        owner_open_id="ou_1", name="blast",
        steps=[SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                                    inputs={"query": "BRCA1"})],
        description="x",
    )
    assert tid


def test_delete_requires_owner():
    svc, repo = _make_service()
    repo.get.return_value = MagicMock(owner_open_id="ou_2",
                                       archived_at=None,
                                       updated_at=datetime.now(UTC))
    with pytest.raises(PermissionError):
        svc.delete(template_id="t1", caller_open_id="ou_1")


def test_delete_owner_succeeds():
    svc, repo = _make_service()
    repo.get.return_value = MagicMock(owner_open_id="ou_1",
                                       archived_at=None,
                                       updated_at=datetime.now(UTC))
    svc.delete(template_id="t1", caller_open_id="ou_1")
    repo.delete.assert_called_once_with("t1")


def test_list_by_owner():
    svc, repo = _make_service()
    repo.list_by_owner.return_value = ["t1", "t2"]
    out = svc.list_by_owner("ou_1")
    assert out == ["t1", "t2"]


def test_render_subplan_substitutes_params():
    svc, repo = _make_service()
    repo.get.return_value = MagicMock(
        template_id="t1", owner_open_id="ou_1", name="x",
        description="", type="subplan",
        steps_json='[{"step_id": "s1", "tool_name": "blast_search",'
                  ' "inputs": {"query": "{{gene}}"}}]',
        blocks_json=None, archived_at=None,
        updated_at=datetime.now(UTC),
    )
    out = svc.render_subplan(template_id="t1", params={"gene": "BRCA1"})
    assert out[0].inputs["query"] == "BRCA1"
