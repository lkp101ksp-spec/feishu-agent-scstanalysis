from unittest.mock import MagicMock

import pytest

from orchestrator.templates.public_service import PublicTemplateService


def _make():
    template_repo = MagicMock()
    template_repo.get.return_value = None
    audit_repo = MagicMock()
    admin_ids = {"admin_1"}
    return PublicTemplateService(
        template_repo=template_repo, audit_repo=audit_repo,
        admin_user_ids=admin_ids,
    ), template_repo, audit_repo


def _tpl(**kwargs):
    spec = ["template_id", "owner_open_id", "name", "type",
            "blocks_json", "steps_json", "description", "scope",
            "chat_id", "archived_at", "lineage_template_id"]
    tpl = MagicMock(spec=spec)
    tpl.archived_at = None
    tpl.lineage_template_id = None
    for k, v in kwargs.items():
        setattr(tpl, k, v)
    return tpl


def test_submit_for_review_owner_only():
    svc, repo, _ = _make()
    repo.get.return_value = None
    repo.get.return_value = _tpl(template_id="t1", owner_open_id="ou_2",
                                  scope="user")
    with pytest.raises(PermissionError):
        svc.submit_for_review(template_id="t1", actor_open_id="ou_1")


def test_submit_for_review_changes_scope_to_pending():
    svc, repo, audit = _make()
    repo.get.return_value = None
    repo.get.return_value = _tpl(template_id="t1", owner_open_id="ou_1",
                                  scope="user")
    svc.submit_for_review(template_id="t1", actor_open_id="ou_1")
    kwargs = repo.upsert.call_args.kwargs
    assert kwargs["scope"] == "public_pending"
    audit.write.assert_called()


def test_approve_requires_admin():
    svc, repo, _ = _make()
    repo.get.return_value = _tpl(template_id="t1", scope="public_pending")
    with pytest.raises(PermissionError):
        svc.approve(template_id="t1", actor_open_id="non_admin")


def test_approve_changes_scope_to_public():
    svc, repo, audit = _make()
    repo.get.return_value = _tpl(template_id="t1", scope="public_pending")
    svc.approve(template_id="t1", actor_open_id="admin_1")
    kwargs = repo.upsert.call_args.kwargs
    assert kwargs["scope"] == "public"
    audit.write.assert_called()


def test_reject_keeps_user_scope_with_reason():
    svc, repo, audit = _make()
    repo.get.return_value = _tpl(template_id="t1", scope="public_pending")
    svc.reject(template_id="t1", actor_open_id="admin_1",
                reason="内容不完整")
    kwargs = repo.upsert.call_args.kwargs
    assert kwargs["scope"] == "user"
    audit_kwargs = audit.write.call_args.kwargs
    assert audit_kwargs["detail"]["reason"] == "内容不完整"


def test_list_public():
    svc, repo, _ = _make()
    repo.list_by_scope.return_value = ["t1", "t2"]
    out = svc.list_public()
    assert out == ["t1", "t2"]


def test_submit_rejects_already_public_pending():
    svc, repo, _ = _make()
    repo.get.return_value = _tpl(owner_open_id="ou_1",
                                  scope="public_pending")
    with pytest.raises(ValueError):
        svc.submit_for_review(template_id="t1", actor_open_id="ou_1")
