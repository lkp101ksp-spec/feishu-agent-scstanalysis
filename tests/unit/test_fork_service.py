from unittest.mock import MagicMock

import pytest

from orchestrator.templates.fork_service import ForkService


def _make():
    repo = MagicMock()
    repo.get.return_value = None
    return ForkService(template_repo=repo), repo


def _tpl(**kw):
    spec = ["template_id", "owner_open_id", "name", "type",
            "blocks_json", "steps_json", "description", "scope",
            "archived_at", "lineage_template_id", "chat_id"]
    tpl = MagicMock(spec=spec)
    tpl.archived_at = None
    tpl.lineage_template_id = None
    tpl.chat_id = None
    for k, v in kw.items():
        setattr(tpl, k, v)
    return tpl


def test_fork_from_public_creates_private_copy():
    svc, repo = _make()
    repo.get.return_value = _tpl(template_id="src", owner_open_id="ou_1",
                                  name="std", type_="block",
                                  blocks_json="[]", steps_json=None,
                                  description="x", scope="public")
    new_id = svc.fork_from_public(
        source_template_id="src", actor_open_id="ou_2",
    )
    assert new_id is not None
    kwargs = repo.upsert.call_args.kwargs
    assert kwargs["owner_open_id"] == "ou_2"
    assert kwargs["scope"] == "user"
    assert kwargs["lineage_template_id"] == "src"
    assert "(fork by ou_2)" in kwargs["name"]


def test_fork_rejects_non_public_template():
    svc, repo = _make()
    repo.get.return_value = _tpl(template_id="src", owner_open_id="ou_1",
                                  scope="user")
    with pytest.raises(PermissionError):
        svc.fork_from_public(source_template_id="src", actor_open_id="ou_1")


def test_fork_raises_if_template_not_found():
    svc, repo = _make()
    repo.get.return_value = None
    with pytest.raises(ValueError):
        svc.fork_from_public(source_template_id="missing",
                              actor_open_id="ou_1")


def test_fork_raises_if_archived():
    svc, repo = _make()
    repo.get.return_value = _tpl(archived_at=True)
    with pytest.raises(ValueError):
        svc.fork_from_public(source_template_id="src",
                              actor_open_id="ou_1")


def test_list_forks():
    svc, repo = _make()
    repo.list_by_lineage.return_value = ["t1", "t2"]
    out = svc.list_forks("src")
    assert out == ["t1", "t2"]
