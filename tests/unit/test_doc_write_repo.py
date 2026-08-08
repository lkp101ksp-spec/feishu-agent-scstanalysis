"""DocWriteRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.doc_write_repo import DocWriteRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_create_pending_with_text_payload(session):
    repo = DocWriteRepo(session)
    row = repo.create_pending(
        doc_write_id="dw1",
        task_id="t1",
        doc_id="doc_a",
        requested_by="ou_x",
        approval_mode="bind_scope",
        payload_text="hello",
    )
    assert row.status == "pending"
    assert row.payload_json == {"text": "hello"}
    assert row.approval_mode == "bind_scope"


def test_transition_pending_to_approved(session):
    repo = DocWriteRepo(session)
    repo.create_pending("dw1", "t1", "doc_a", "ou_x", "bind_scope", "hello")
    session.commit()
    repo.transition(doc_write_id="dw1", to_status="approved")
    session.commit()
    assert repo.get("dw1").status == "approved"


def test_mark_success_records_anchor(session):
    repo = DocWriteRepo(session)
    repo.create_pending("dw1", "t1", "doc_a", "ou_x", "bind_scope", "hello")
    session.commit()
    repo.mark_success(doc_write_id="dw1", anchor_block_id="blk_x")
    session.commit()
    row = repo.get("dw1")
    assert row.status == "success"
    assert row.anchor_block_id == "blk_x"


def test_mark_failed_records_reason(session):
    repo = DocWriteRepo(session)
    repo.create_pending("dw1", "t1", "doc_a", "ou_x", "bind_scope", "hello")
    session.commit()
    repo.mark_failed(doc_write_id="dw1", reason="lark-cli timeout")
    session.commit()
    row = repo.get("dw1")
    assert row.status == "failed"
    assert row.fail_reason == "lark-cli timeout"


def test_get_unknown_returns_none(session):
    repo = DocWriteRepo(session)
    assert repo.get("nope") is None