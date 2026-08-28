from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from orchestrator.bind_doc_service import BindDocService
from shared.errors import BindDocInvalidError


class FakeSession:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _make_service(*, ttl_sec=1800, im_adapter=None,
                   renew_threshold_sec=300):
    fake_audit = MagicMock()
    fake_session_svc = MagicMock()
    fake_session_repo = MagicMock()
    return BindDocService(
        session_service=fake_session_svc,
        audit_repo=fake_audit,
        ttl_sec=ttl_sec,
        session_repo=fake_session_repo,
        im_adapter=im_adapter,
        renew_threshold_sec=renew_threshold_sec,
    ), fake_session_repo, fake_audit


def test_renew_extends_expires_at():
    svc, session_repo, _ = _make_service(ttl_sec=1800)
    session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
    )
    new_exp = svc.renew(session_id="s1")
    assert new_exp > datetime.now(timezone.utc) + timedelta(seconds=1700)


def test_renew_no_active_bind_raises():
    svc, session_repo, _ = _make_service()
    session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id=None, bind_expires_at=None,
    )
    with pytest.raises(BindDocInvalidError):
        svc.renew(session_id="s1")


def test_renew_never_shortens():
    far_future = datetime.now(timezone.utc) + timedelta(hours=2)
    svc, session_repo, _ = _make_service(ttl_sec=1800)
    session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id="d1", bind_expires_at=far_future,
    )
    new_exp = svc.renew(session_id="s1")
    # 不应缩短
    assert new_exp >= far_future


async def test_maybe_send_renew_card_when_remaining_le_5min():
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=100),
                    source_chat_id="chat_1"),
    ]
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
        im_adapter=fake_im, renew_threshold_sec=300,
    )
    await svc.maybe_send_renew_card()
    fake_im.send_card.assert_called_once()


async def test_maybe_send_renew_card_no_card_when_far():
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=1800),
                    source_chat_id="chat_1"),
    ]
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
        im_adapter=fake_im, renew_threshold_sec=300,
    )
    await svc.maybe_send_renew_card()
    fake_im.send_card.assert_not_called()


async def test_maybe_send_renew_card_dedup_same_expiry():
    """同一 (session, expires_at) 只发一次：两轮扫描不重发。"""
    fake_im = MagicMock()
    expires = datetime.now(timezone.utc) + timedelta(seconds=100)
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=expires, source_chat_id="chat_1"),
    ]
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
        im_adapter=fake_im, renew_threshold_sec=300,
    )
    await svc.maybe_send_renew_card()
    await svc.maybe_send_renew_card()
    fake_im.send_card.assert_called_once()


async def test_maybe_send_renew_card_resends_after_expiry_changes():
    """续期后 expires_at 变化：新一轮临期会再次发卡（key 含 expires）。"""
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=100),
                    source_chat_id="chat_1"),
    ]
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
        im_adapter=fake_im, renew_threshold_sec=300,
    )
    await svc.maybe_send_renew_card()
    # 模拟续期：过期时间推到新的临期点
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=50),
                    source_chat_id="chat_1"),
    ]
    await svc.maybe_send_renew_card()
    assert fake_im.send_card.call_count == 2


async def test_maybe_send_renew_card_button_text_follows_ttl():
    """按钮文案随 TTL 配置（短 TTL 显示秒）。"""
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=100),
                    source_chat_id="chat_1"),
    ]
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=180, session_repo=fake_session_repo,
        im_adapter=fake_im, renew_threshold_sec=300,
    )
    await svc.maybe_send_renew_card()
    card = fake_im.send_card.call_args.kwargs["card"]
    btn = card["elements"][0]["actions"][0]
    assert btn["text"]["content"] == "续期 3 分钟"
