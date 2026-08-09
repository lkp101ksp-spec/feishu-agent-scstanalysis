import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.execution_repo import ExecutionRepo


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


def test_create_and_get(session):
    repo = ExecutionRepo(session)
    eid = repo.create(
        task_id="t1",
        plan_id="p1",
        node_id="n1",
        tool_name="run_python",
        risk_level="L1_compute",
        inputs_json={"code": "1+1"},
    )
    e = repo.get(eid)
    assert e.tool_name == "run_python"
    assert e.state == "pending"


def test_finish_success(session):
    repo = ExecutionRepo(session)
    eid = repo.create(
        task_id="t1", plan_id="p1", node_id="n1",
        tool_name="x", risk_level="L0_read", inputs_json={},
    )
    repo.finish(eid, state="success", outputs_json={"r": 1})
    assert repo.get(eid).state == "success"


def test_list_by_plan(session):
    repo = ExecutionRepo(session)
    for i in range(3):
        repo.create(
            task_id="t1", plan_id="p1", node_id=f"n{i}",
            tool_name="x", risk_level="L0_read", inputs_json={},
        )
    assert len(repo.list_by_plan("p1")) == 3