import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.approval_repo import ApprovalRepo


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def test_create_pending(session):
    repo = ApprovalRepo(session)
    aid = repo.create(
        task_id="t1",
        tool_name="write_doc",
        args_preview={"doc_id": "d1"},
        actor_open_id="ou_1",
        session_id="s1",
        nonce="abc123",
        expires_at="2099-01-01T00:00:00",
    )
    a = repo.get(aid)
    assert a.status == "pending"


def test_resolve_approved(session):
    repo = ApprovalRepo(session)
    aid = repo.create(
        task_id="t1", tool_name="write_doc", args_preview={},
        actor_open_id="ou_1", session_id="s1", nonce="n1",
        expires_at="2099-01-01T00:00:00",
    )
    repo.resolve(aid, status="approved", resolved_by="ou_1")
    assert repo.get(aid).status == "approved"


def test_get_by_nonce(session):
    repo = ApprovalRepo(session)
    repo.create(
        task_id="t1", tool_name="x", args_preview={},
        actor_open_id="ou_1", session_id="s1",
        nonce="unique-nonce", expires_at="2099-01-01T00:00:00",
    )
    a = repo.get_by_nonce("unique-nonce")
    assert a is not None
    assert a.task_id == "t1"