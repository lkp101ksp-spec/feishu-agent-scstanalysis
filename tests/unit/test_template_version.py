import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_version_repo import TemplateVersionRepo


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


def test_insert_and_count(session):
    repo = TemplateVersionRepo(session)
    repo.insert(version_id="v1", template_id="t1", version_number=1,
                name="n", description="d", blocks_json="[]", steps_json=None,
                created_by="ou_1")
    repo.insert(version_id="v2", template_id="t1", version_number=2,
                name="n2", description="d", blocks_json="[]", steps_json=None,
                created_by="ou_1")
    session.commit()
    assert repo.count("t1") == 2


def test_list_by_template(session):
    repo = TemplateVersionRepo(session)
    for i in range(3):
        repo.insert(version_id=f"v{i+1}", template_id="t1",
                    version_number=i+1, name="n", description="d",
                    blocks_json=None, steps_json=None, created_by="ou_1")
    session.commit()
    out = repo.list_by_template("t1")
    assert len(out) == 3
    assert out[0].version_number == 3  # DESC


def test_get_by_version(session):
    repo = TemplateVersionRepo(session)
    repo.insert(version_id="v2", template_id="t1", version_number=2,
                name="n", description="d", blocks_json="[x]", steps_json=None,
                created_by="ou_1")
    session.commit()
    v = repo.get_by_version("t1", 2)
    assert v.blocks_json == "[x]"