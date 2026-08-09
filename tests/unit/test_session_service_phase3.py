from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from orchestrator.session_service import SessionService


class FakeSession:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_freeze_session_returns_new_id_with_inherited_bind():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
        approval_scope={}, owner_open_id="ou_1", source_chat_id="chat_1",
        archived_at=None, origin_session_id=None,
    )
    fake_freeze_repo = MagicMock()
    svc = SessionService(repo=fake_session_repo,
                          freeze_repo=fake_freeze_repo,
                          audit_repo=MagicMock())
    new_sid = svc.freeze_session(session_id="s1", summary="x",
                                  trigger_ratio=0.97)
    assert new_sid != "s1"
    # 检查 upsert 调用找到 new session
    upsert_calls = fake_session_repo.upsert.call_args_list
    # 最后一次调用是创建新 session
    last_call = upsert_calls[-1]
    assert last_call.kwargs["bound_doc_id"] == "d1"
    assert last_call.kwargs["origin_session_id"] == "s1"
    # 新 session_id 应该是 ULID（不等于 "s1"）
    assert last_call.kwargs["session_id"] == new_sid


def test_freeze_session_expired_bind_not_inherited():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        approval_scope={}, owner_open_id="ou_1", source_chat_id="chat_1",
        archived_at=None, origin_session_id=None,
    )
    fake_freeze_repo = MagicMock()
    svc = SessionService(repo=fake_session_repo,
                          freeze_repo=fake_freeze_repo,
                          audit_repo=MagicMock())
    new_sid = svc.freeze_session(session_id="s1", summary="x",
                                  trigger_ratio=0.97)
    last_call = fake_session_repo.upsert.call_args_list[-1]
    assert last_call.kwargs["bound_doc_id"] is None