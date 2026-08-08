"""TaskRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.task_repo import TaskRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_create_and_get_by_message_id(session):
    repo = TaskRepo(session)
    repo.create(task_id="t1", session_id="s1", message_id="om_x", intent="general_chat")
    session.commit()
    got = repo.get_by_message_id("om_x")
    assert got is not None
    assert got.task_id == "t1"
    assert got.intent == "general_chat"


def test_update_status_to_success_sets_reply(session):
    repo = TaskRepo(session)
    repo.create(task_id="t1", session_id="s1", message_id="om_x")
    session.commit()
    repo.update_status(task_id="t1", status="success", reply_text="done")
    session.commit()
    got = repo.get("t1")
    assert got.status == "success"
    assert got.reply_text == "done"
    assert got.finished_at is not None


def test_update_status_to_failed_records_error(session):
    repo = TaskRepo(session)
    repo.create(task_id="t1", session_id="s1", message_id="om_x")
    session.commit()
    repo.update_status(task_id="t1", status="failed", error_code="LLM_TIMEOUT", error_message="timeout")
    session.commit()
    got = repo.get("t1")
    assert got.status == "failed"
    assert got.error_code == "LLM_TIMEOUT"
    assert got.error_message == "timeout"


def test_update_status_does_not_set_finished_for_running(session):
    repo = TaskRepo(session)
    repo.create(task_id="t1", session_id="s1", message_id="om_x")
    session.commit()
    repo.update_status(task_id="t1", status="running")
    session.commit()
    got = repo.get("t1")
    assert got.status == "running"
    assert got.finished_at is None


def test_get_unknown_returns_none(session):
    repo = TaskRepo(session)
    assert repo.get("nope") is None
    assert repo.get_by_message_id("nope") is None