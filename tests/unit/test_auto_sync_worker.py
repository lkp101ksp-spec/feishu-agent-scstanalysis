"""Phase 9 T5: CommentAutoSyncWorker 单元测试（SQLite 真 session_repo + stub 服务）。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import MagicMock

from orchestrator.templates.auto_sync_worker import CommentAutoSyncWorker
from persistence.models import Base
from persistence.repositories.session_repo import SessionRepo


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _worker(session):
    sync = MagicMock()
    sync.sync.return_value = {"fetched": 0, "new": 0, "updated": 0}
    notify = MagicMock()
    notify.notify_new_pending.return_value = {"notified": 0}
    w = CommentAutoSyncWorker(
        session_repo=SessionRepo(session),
        sync_service=sync, notify_service=notify, interval_sec=60,
    )
    return w, sync, notify


def _bind(session, sid, doc, chat="oc_1", owner="ou_1", expires_in=600):
    SessionRepo(session).upsert(
        session_id=sid, owner_open_id=owner, source_chat_id=chat,
        bound_doc_id=doc,
        bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )


def test_tick_syncs_bound_doc(session):
    w, sync, notify = _worker(session)
    _bind(session, "s1", "doc_1")
    out = w.tick()
    assert out == {"synced": 1, "notified_total": 0}
    sync.sync.assert_called_once_with(doc_id="doc_1")
    notify.notify_new_pending.assert_called_once_with(
        doc_id="doc_1", owner_open_id="ou_1", chat_id="oc_1")


def test_skips_expired_bind(session):
    w, sync, notify = _worker(session)
    _bind(session, "s1", "doc_1", expires_in=-10)  # 已过期
    out = w.tick()
    assert out == {"synced": 0, "notified_total": 0}
    sync.sync.assert_not_called()


def test_skips_unbound(session):
    w, sync, notify = _worker(session)
    SessionRepo(session).upsert(
        session_id="s1", owner_open_id="ou_1", source_chat_id="oc_1",
        bound_doc_id=None, bind_expires_at=None,
    )
    out = w.tick()
    assert out == {"synced": 0, "notified_total": 0}
    sync.sync.assert_not_called()


def test_same_doc_dedup(session):
    w, sync, notify = _worker(session)
    _bind(session, "s1", "doc_1", chat="oc_1", owner="ou_1")
    _bind(session, "s2", "doc_1", chat="oc_2", owner="ou_2")
    out = w.tick()
    assert out["synced"] == 1
    sync.sync.assert_called_once()


def test_sync_exception_isolated(session):
    w, sync, notify = _worker(session)
    _bind(session, "s1", "doc_bad")
    _bind(session, "s2", "doc_ok")
    sync.sync.side_effect = lambda *, doc_id: (
        (_ for _ in ()).throw(RuntimeError("api down"))
        if doc_id == "doc_bad" else {"fetched": 0}
    )
    out = w.tick()
    assert out["synced"] == 1  # doc_ok 成功，doc_bad 被隔离
