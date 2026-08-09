import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    return e


def test_plan_runtime_state_table_created(engine):
    insp = inspect(engine)
    assert "plan_runtime_state" in insp.get_table_names()


def test_session_freezes_table_created(engine):
    insp = inspect(engine)
    assert "session_freezes" in insp.get_table_names()


def test_executions_has_loop_fields(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("executions")}
    assert "loop_id" in cols
    assert "loop_iteration" in cols
    assert "dynamic_parent_id" in cols


def test_sessions_has_archived_at(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("sessions")}
    assert "archived_at" in cols
    assert "origin_session_id" in cols
    assert "token_count" in cols