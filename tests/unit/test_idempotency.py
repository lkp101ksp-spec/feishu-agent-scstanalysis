"""idempotency 工具测试：build_idempotency_key + IdempotencyRepo 端到端。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gateway.idempotency import build_idempotency_key, link_task, try_reserve
from persistence.models import Base, IdempotencyKeyRow
from persistence.repositories.idempotency_repo import IdempotencyRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_build_idempotency_key_format():
    key = build_idempotency_key("cli_abc", "oc_xyz", "om_123")
    assert key == "cli_abc:oc_xyz:om_123"


def test_try_reserve_first_call_succeeds(session):
    repo = IdempotencyRepo(session)
    assert try_reserve(repo, "app1:oc1:om1") is True
    session.commit()


def test_try_reserve_second_call_rejected(session):
    repo = IdempotencyRepo(session)
    try_reserve(repo, "app1:oc1:om1")
    session.commit()
    assert try_reserve(repo, "app1:oc1:om1") is False


def test_link_task_after_reserve(session):
    repo = IdempotencyRepo(session)
    try_reserve(repo, "app1:oc1:om1")
    session.commit()
    link_task(repo, "app1:oc1:om1", "t_xxx")
    session.commit()
    row = session.get(IdempotencyKeyRow, "app1:oc1:om1")
    assert row.task_id == "t_xxx"


def test_try_reserve_passes_task_id(session):
    repo = IdempotencyRepo(session)
    assert try_reserve(repo, "app1:oc1:om1", task_id="t_init") is True
    session.commit()
    row = session.get(IdempotencyKeyRow, "app1:oc1:om1")
    assert row.task_id == "t_init"
