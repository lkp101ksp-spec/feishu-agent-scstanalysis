import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_repo import TemplateRepo


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


def test_template_upsert_and_get(session):
    repo = TemplateRepo(session)
    repo.upsert(
        template_id="t1", owner_open_id="ou_1",
        name="std_report", type_="block",
        blocks_json='[{"type":"text","text":"hi"}]',
        steps_json=None, description="x",
    )
    session.commit()
    t = repo.get("t1")
    assert t.owner_open_id == "ou_1"
    assert t.type == "block"


def test_template_list_by_owner(session):
    repo = TemplateRepo(session)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="a", type_="block", blocks_json=None,
                steps_json=None, description="")
    repo.upsert(template_id="t2", owner_open_id="ou_1",
                name="b", type_="subplan", blocks_json=None,
                steps_json='[]', description="")
    repo.upsert(template_id="t3", owner_open_id="ou_2",
                name="c", type_="block", blocks_json=None,
                steps_json=None, description="")
    session.commit()
    out = repo.list_by_owner("ou_1")
    assert len(out) == 2


def test_template_delete_soft(session):
    repo = TemplateRepo(session)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="a", type_="block", blocks_json=None,
                steps_json=None, description="")
    session.commit()
    repo.delete("t1")
    session.commit()
    t = repo.get("t1")
    assert t.archived_at is not None
    # list_by_owner 不应返回
    assert repo.list_by_owner("ou_1") == []
