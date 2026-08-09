from unittest.mock import MagicMock

import pytest

from orchestrator.templates.share_service import ShareService


def _make():
    template_repo = MagicMock()
    template_repo.list_by_chat.return_value = []
    template_repo.get.return_value = None
    return ShareService(template_repo=template_repo), template_repo


def test_share_to_chat_requires_owner():
    svc, repo = _make()
    tpl = MagicMock(spec=["owner_open_id", "archived_at"])
    tpl.owner_open_id = "ou_2"
    tpl.archived_at = None
    repo.get.return_value = tpl
    with pytest.raises(PermissionError):
        svc.share_to_chat(template_id="t1", chat_id="chat_1",
                          caller_open_id="ou_1")


def test_share_to_chat_owner_succeeds():
    svc, repo = _make()
    tpl = MagicMock(spec=["owner_open_id", "archived_at", "name",
                            "type", "blocks_json", "steps_json", "description"])
    tpl.owner_open_id = "ou_1"
    tpl.archived_at = None
    tpl.name = "x"
    tpl.type = "block"
    tpl.blocks_json = "[]"
    tpl.steps_json = None
    tpl.description = ""
    repo.get.return_value = tpl
    svc.share_to_chat(template_id="t1", chat_id="chat_1",
                      caller_open_id="ou_1")
    repo.upsert.assert_called_once()
    kwargs = repo.upsert.call_args.kwargs
    assert kwargs["scope"] == "chat"
    assert kwargs["chat_id"] == "chat_1"


def test_list_for_chat():
    svc, repo = _make()
    repo.list_by_chat.return_value = ["t1", "t2"]
    out = svc.list_for_chat("chat_1")
    assert out == ["t1", "t2"]


def test_can_access_user_scope_owner():
    svc, repo = _make()
    tpl = MagicMock(spec=["scope", "owner_open_id", "archived_at"])
    tpl.scope = "user"
    tpl.owner_open_id = "ou_1"
    tpl.archived_at = None
    repo.get.return_value = tpl
    assert svc.can_access(template_id="t1", caller_open_id="ou_1",
                           chat_id=None) is True


def test_can_access_chat_scope_same_chat():
    svc, repo = _make()
    tpl = MagicMock(spec=["scope", "chat_id", "owner_open_id", "archived_at"])
    tpl.scope = "chat"
    tpl.chat_id = "chat_1"
    tpl.owner_open_id = "ou_1"
    tpl.archived_at = None
    repo.get.return_value = tpl
    assert svc.can_access(template_id="t1", caller_open_id="ou_2",
                           chat_id="chat_1") is True


def test_can_access_chat_scope_different_chat():
    svc, repo = _make()
    tpl = MagicMock(spec=["scope", "chat_id", "owner_open_id", "archived_at"])
    tpl.scope = "chat"
    tpl.chat_id = "chat_1"
    tpl.owner_open_id = "ou_1"
    tpl.archived_at = None
    repo.get.return_value = tpl
    assert svc.can_access(template_id="t1", caller_open_id="ou_2",
                           chat_id="chat_2") is False