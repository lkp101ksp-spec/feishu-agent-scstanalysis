"""Phase 9 T1: CommentNotifyRepo 单元测试（SQLite 真库）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.comment_notify_repo import CommentNotifyRepo


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


def test_insert_and_has(session):
    repo = CommentNotifyRepo(session)
    assert repo.has("c1") is False
    n = repo.insert_many(doc_id="d1", comment_ids=["c1"])
    assert n == 1
    assert repo.has("c1") is True
    # 重复插入幂等
    assert repo.insert_many(doc_id="d1", comment_ids=["c1"]) == 0


def test_insert_many_partial_new(session):
    repo = CommentNotifyRepo(session)
    repo.insert_many(doc_id="d1", comment_ids=["c1", "c2"])
    n = repo.insert_many(doc_id="d1", comment_ids=["c2", "c3", "c4"])
    assert n == 2  # 仅 c3/c4 新增
    assert sorted(repo.list_by_doc("d1")) == ["c1", "c2", "c3", "c4"]


def test_list_by_doc_filters(session):
    repo = CommentNotifyRepo(session)
    repo.insert_many(doc_id="d1", comment_ids=["c1"])
    repo.insert_many(doc_id="d2", comment_ids=["c2", "c3"])
    assert repo.list_by_doc("d2") == ["c2", "c3"]
    assert repo.list_by_doc("d1") == ["c1"]
