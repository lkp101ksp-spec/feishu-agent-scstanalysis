"""bind-doc 服务测试。"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from orchestrator.bind_doc_service import BindDocService
from shared.errors import BindDocInvalidError


def test_bind_doc_writes_to_session_and_audit():
    session_svc = MagicMock()
    session_svc.bind_doc.return_value = datetime(2026, 8, 8, 10, 0, 0)
    audit_repo = MagicMock()
    service = BindDocService(session_service=session_svc, audit_repo=audit_repo, ttl_sec=1800)

    expires_at = service.bind(session_id="s1", owner_open_id="ou_x", doc_id="doccnABC123")

    session_svc.bind_doc.assert_called_once_with(session_id="s1", doc_id="doccnABC123", ttl_sec=1800)
    audit_repo.write.assert_called_once()
    audit_kwargs = audit_repo.write.call_args.kwargs
    assert audit_kwargs["actor_type"] == "user"
    assert audit_kwargs["actor_id"] == "ou_x"
    assert audit_kwargs["action"] == "bind_doc"
    assert audit_kwargs["target_type"] == "session"
    assert audit_kwargs["target_id"] == "s1"
    assert audit_kwargs["detail"]["doc_id"] == "doccnABC123"
    assert isinstance(expires_at, datetime)


def test_bind_doc_rejects_empty_doc_id():
    service = BindDocService(session_service=MagicMock(), audit_repo=MagicMock(), ttl_sec=1800)
    with pytest.raises(BindDocInvalidError) as exc:
        service.bind(session_id="s1", owner_open_id="ou_x", doc_id="")
    assert "invalid" in str(exc.value).lower()


def test_bind_doc_rejects_invalid_format_with_spaces():
    service = BindDocService(session_service=MagicMock(), audit_repo=MagicMock(), ttl_sec=1800)
    with pytest.raises(BindDocInvalidError):
        service.bind(session_id="s1", owner_open_id="ou_x", doc_id="invalid id with spaces")


def test_bind_doc_rejects_too_short_doc_id():
    service = BindDocService(session_service=MagicMock(), audit_repo=MagicMock(), ttl_sec=1800)
    with pytest.raises(BindDocInvalidError):
        service.bind(session_id="s1", owner_open_id="ou_x", doc_id="abc")


def test_bind_doc_accepts_alphanumeric_and_dash():
    session_svc = MagicMock()
    audit_repo = MagicMock()
    service = BindDocService(session_service=session_svc, audit_repo=audit_repo, ttl_sec=1800)

    service.bind(session_id="s1", owner_open_id="ou_x", doc_id="doccn_ABC-123")
    session_svc.bind_doc.assert_called_once()
