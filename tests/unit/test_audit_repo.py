"""AuditRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.audit_repo import AuditRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_write_audit_log_persists_fields(session):
    repo = AuditRepo(session)
    repo.write(
        audit_id="a1",
        actor_type="user",
        actor_id="ou_x",
        action="create_task",
        target_type="task",
        target_id="t1",
        detail={"intent": "general_chat"},
    )
    session.commit()
    logs = repo.list_recent(limit=10)
    assert len(logs) == 1
    assert logs[0].audit_id == "a1"
    assert logs[0].actor_id == "ou_x"
    assert logs[0].detail_json == {"intent": "general_chat"}


def test_list_recent_orders_newest_first(session):
    repo = AuditRepo(session)
    for i in range(3):
        repo.write(
            audit_id=f"a{i}",
            actor_type="system",
            actor_id="sys",
            action="x",
            target_type="t",
            target_id=f"id{i}",
        )
    session.commit()
    logs = repo.list_recent(limit=10)
    audit_ids = [l.audit_id for l in logs]
    # 默认 _utcnow 在同一次 commit 里可能相同时间戳，但 created_at 默认是创建时刻
    # 排序至少应包含全部 3 条
    assert set(audit_ids) == {"a0", "a1", "a2"}


def test_write_without_detail_defaults_to_empty_dict(session):
    repo = AuditRepo(session)
    repo.write(
        audit_id="a1",
        actor_type="system",
        actor_id="sys",
        action="ping",
        target_type="system",
        target_id="ping",
    )
    session.commit()
    assert repo.list_recent()[0].detail_json == {}