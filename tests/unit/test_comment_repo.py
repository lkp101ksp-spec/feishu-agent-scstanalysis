"""Phase 8 T2: CommentRepo 单元测试（SQLite 真库）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.comment_repo import CommentRepo


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


def test_upsert_insert_then_idempotent_update(session):
    repo = CommentRepo(session)
    repo.upsert_one(comment_id="c1", doc_id="d1", user_name="U", text="v1")
    repo.upsert_one(comment_id="c1", doc_id="d1", user_name="U", text="v2")
    rows = repo.list_by_doc("d1")
    assert len(rows) == 1
    assert rows[0].text == "v2"


def test_upsert_preserves_processed_at(session):
    repo = CommentRepo(session)
    repo.upsert_one(comment_id="c1", doc_id="d1", text="v1")
    repo.mark_processed("c1")
    repo.upsert_one(comment_id="c1", doc_id="d1", text="v2")
    rows = repo.list_by_doc("d1")
    assert rows[0].processed_at is not None


def test_list_pending_filters_processed(session):
    repo = CommentRepo(session)
    repo.upsert_one(comment_id="c1", doc_id="d1", text="a")
    repo.upsert_one(comment_id="c2", doc_id="d1", text="b")
    repo.upsert_one(comment_id="c3", doc_id="d1", text="c")
    repo.mark_processed("c3")
    pending = repo.list_pending("d1")
    assert [r.comment_id for r in pending] == ["c1", "c2"]


def test_list_by_doc_block_filter(session):
    repo = CommentRepo(session)
    repo.upsert_one(comment_id="c1", doc_id="d1", block_id="b1", text="x")
    repo.upsert_one(comment_id="c2", doc_id="d1", block_id="b2", text="y")
    hits = repo.list_by_doc("d1", block_id="b1")
    assert [r.comment_id for r in hits] == ["c1"]
