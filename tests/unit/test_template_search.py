from unittest.mock import MagicMock

from orchestrator.templates.search_service import TemplateSearchService


def _make():
    template_repo = MagicMock()
    template_repo.search.return_value = ["t1", "t2"]
    return TemplateSearchService(template_repo=template_repo), template_repo


def test_search_basic():
    svc, repo = _make()
    out = svc.search(query="blast")
    repo.search.assert_called_once_with(
        query="blast", scope=None, owner_open_id=None,
        limit=20, offset=0,
    )
    assert out == ["t1", "t2"]


def test_search_with_scope():
    svc, repo = _make()
    out = svc.search(query="x", scope="public")
    repo.search.assert_called_once_with(
        query="x", scope="public", owner_open_id=None,
        limit=20, offset=0,
    )


def test_search_with_pagination():
    svc, repo = _make()
    out = svc.search(query="x", limit=50, offset=10)
    repo.search.assert_called_once_with(
        query="x", scope=None, owner_open_id=None,
        limit=50, offset=10,
    )


def test_search_with_owner():
    svc, repo = _make()
    out = svc.search(query="x", owner_open_id="ou_1")
    repo.search.assert_called_once_with(
        query="x", scope=None, owner_open_id="ou_1",
        limit=20, offset=0,
    )
