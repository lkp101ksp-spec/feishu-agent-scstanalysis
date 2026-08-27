import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.plan_runtime_state_repo import PlanRuntimeStateRepo
from persistence.repositories.session_freeze_repo import SessionFreezeRepo


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


def test_runtime_state_upsert_and_get(session):
    repo = PlanRuntimeStateRepo(session)
    repo.upsert(plan_id="p1", session_id="s1", state_json={"loop_counters": {}})
    state = repo.get("p1")
    assert state.session_id == "s1"
    assert state.state_json == {"loop_counters": {}}
    # upsert update
    repo.upsert(plan_id="p1", session_id="s1", state_json={"loop_counters": {"x": 1}})
    assert repo.get("p1").state_json == {"loop_counters": {"x": 1}}


def test_freeze_create_and_list(session):
    repo = SessionFreezeRepo(session)
    fid = repo.create(
        origin_session_id="so", new_session_id="sn",
        summary_id="s_1", trigger_ratio=0.97,
    )
    f = repo.get(fid)
    assert f.origin_session_id == "so"
    assert f.trigger_ratio == 0.97
    assert len(repo.list_by_origin("so")) == 1
