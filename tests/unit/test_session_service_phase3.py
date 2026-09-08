from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.session_service import SessionService
from persistence.models import AuditLogRow, Base, SessionFreezeRow, SessionRow
from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.session_freeze_repo import SessionFreezeRepo
from persistence.repositories.session_repo import SessionRepo


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


# === 真库回归（2026-09-08）：MagicMock 曾掩盖 upsert 契约漂移，致 freeze 对真实 repo 必崩 ===


@pytest.fixture
def real_session():
    """sqlite 内存真库（StaticPool 共享连接，与既有真库测试同模式）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_origin(s, *, bind_ttl=600):
    """造一个带绑定与审批范围的活跃 session 行；bind_ttl<0 表示已过期。"""
    SessionRepo(s).upsert(
        session_id="s1", owner_open_id="ou_1", source_chat_id="chat_1",
        bound_doc_id="d1",
        bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=bind_ttl),
    )
    s.query(SessionRow).filter_by(session_id="s1").one().approval_scope = {"a": 1}
    s.flush()


def test_freeze_session_realdb_full_flow(real_session):
    """真库全链路：旧行归档且 bind 原值保留，新行继承 bind/scope/origin，freeze 落库。

    audit_repo 用真件（2026-09-09 真机教训：None/MagicMock 掩盖了 write 缺 audit_id）。
    """
    _seed_origin(real_session)
    svc = SessionService(repo=SessionRepo(real_session),
                         freeze_repo=SessionFreezeRepo(real_session),
                         audit_repo=AuditRepo(real_session))
    new_sid = svc.freeze_session(session_id="s1", summary="x",
                                 trigger_ratio=0.97)

    old = real_session.query(SessionRow).filter_by(session_id="s1").one()
    assert old.status == "archived" and old.archived_at is not None
    # 归档只改状态：bind 字段是历史记录，_UNSET 哨兵语义下必须原样保留
    assert old.bound_doc_id == "d1" and old.bind_expires_at is not None

    new = real_session.query(SessionRow).filter_by(session_id=new_sid).one()
    assert new.status == "active"
    assert new.bound_doc_id == "d1"
    assert new.bind_expires_at == old.bind_expires_at
    assert new.approval_scope == {"a": 1}
    assert new.origin_session_id == "s1"

    fr = real_session.query(SessionFreezeRow).filter_by(origin_session_id="s1").one()
    assert fr.new_session_id == new_sid
    assert abs(fr.trigger_ratio - 0.97) < 1e-9

    audits = real_session.query(AuditLogRow).filter_by(action="freeze_session").all()
    assert len(audits) == 1 and audits[0].audit_id and audits[0].target_id == "s1"


def test_freeze_session_realdb_expired_bind_not_inherited(real_session):
    """真库：绑定已过期的 session 冻结后，新 session 不继承 bind。"""
    _seed_origin(real_session, bind_ttl=-10)
    svc = SessionService(repo=SessionRepo(real_session),
                         freeze_repo=SessionFreezeRepo(real_session))
    new_sid = svc.freeze_session(session_id="s1", summary="x",
                                 trigger_ratio=0.97)
    new = real_session.query(SessionRow).filter_by(session_id=new_sid).one()
    assert new.bound_doc_id is None and new.bind_expires_at is None


def test_upsert_partial_update_sentinel_semantics(real_session):
    """真库钉 _UNSET 哨兵：未传的 bind 字段保持原值，显式传 None 才清除。"""
    _seed_origin(real_session)
    repo = SessionRepo(real_session)
    repo.upsert(session_id="s1", archived_at=datetime.now(timezone.utc),
                status="archived")
    real_session.expire_all()
    row = real_session.query(SessionRow).filter_by(session_id="s1").one()
    assert row.status == "archived"
    assert row.bound_doc_id == "d1"  # 未传 → 保留
    repo.upsert(session_id="s1", bound_doc_id=None)  # 显式 None → 清除
    real_session.expire_all()
    row = real_session.query(SessionRow).filter_by(session_id="s1").one()
    assert row.bound_doc_id is None
