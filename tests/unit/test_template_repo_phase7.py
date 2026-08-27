from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_repo import TemplateRepo


def _make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_search_by_name():
    s = _make_session()
    repo = TemplateRepo(s)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="blast_workflow", type_="subplan",
                blocks_json=None, steps_json="[]", description="x")
    repo.upsert(template_id="t2", owner_open_id="ou_1",
                name="std_report", type_="block",
                blocks_json="[]", steps_json=None, description="y")
    s.commit()
    out = repo.search(query="blast")
    assert len(out) == 1
    assert out[0].name == "blast_workflow"


def test_search_by_description():
    s = _make_session()
    repo = TemplateRepo(s)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="std", type_="block", blocks_json="[]", steps_json=None,
                description="report template")
    s.commit()
    out = repo.search(query="report")
    assert len(out) == 1


def test_search_filters_by_scope():
    s = _make_session()
    repo = TemplateRepo(s)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="std", type_="block", blocks_json="[]", steps_json=None,
                description="x", scope="public")
    repo.upsert(template_id="t2", owner_open_id="ou_1",
                name="std2", type_="block", blocks_json="[]", steps_json=None,
                description="x", scope="user")
    s.commit()
    out = repo.search(query="std", scope="public")
    assert len(out) == 1
    assert out[0].template_id == "t1"


def test_list_by_scope():
    s = _make_session()
    repo = TemplateRepo(s)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="std", type_="block", blocks_json="[]", steps_json=None,
                description="x", scope="public")
    s.commit()
    out = repo.list_by_scope(scope="public")
    assert len(out) == 1


def test_list_by_lineage():
    s = _make_session()
    repo = TemplateRepo(s)
    repo.upsert(template_id="t2", owner_open_id="ou_2",
                name="fork", type_="block", blocks_json="[]", steps_json=None,
                description="x", scope="user",
                lineage_template_id="t1")
    s.commit()
    out = repo.list_by_lineage("t1")
    assert len(out) == 1
    assert out[0].template_id == "t2"


def test_search_respects_limit():
    s = _make_session()
    repo = TemplateRepo(s)
    for i in range(5):
        repo.upsert(template_id=f"t{i}", owner_open_id="ou_1",
                    name="std", type_="block", blocks_json="[]",
                    steps_json=None, description="x")
    s.commit()
    out = repo.search(query="std", limit=2)
    assert len(out) == 2
