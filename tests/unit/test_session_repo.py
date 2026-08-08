"""SessionRepo 测试（用 SQLite 内存库）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.session_repo import SessionRepo


@pytest.fixture
def session():
    """每个测试一个全新的内存 SQLite session。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_upsert_creates_new(session):
    repo = SessionRepo(session)
    row = repo.upsert(
        session_id="s1",
        owner_open_id="ou_x",
        source_chat_id="oc_x",
        bound_doc_id="doc_a",
        bind_expires_at=None,
    )
    assert row.session_id == "s1"
    assert row.bound_doc_id == "doc_a"
    assert row.owner_open_id == "ou_x"


def test_upsert_updates_existing(session):
    repo = SessionRepo(session)
    repo.upsert(session_id="s1", owner_open_id="ou_x", source_chat_id="oc_x", bound_doc_id="doc_a", bind_expires_at=None)
    session.commit()
    repo.upsert(session_id="s1", owner_open_id="ou_x", source_chat_id="oc_x", bound_doc_id="doc_b", bind_expires_at=None)
    session.commit()
    got = repo.get("s1")
    assert got.bound_doc_id == "doc_b"


def test_get_returns_none_for_unknown(session):
    repo = SessionRepo(session)
    assert repo.get("nonexistent") is None