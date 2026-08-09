import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_audit_repo import TemplateAuditRepo


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


def test_insert_and_list(session):
    repo = TemplateAuditRepo(session)
    repo.insert(audit_id="a1", template_id="t1", action="submit",
                actor_open_id="ou_1", reason=None)
    repo.insert(audit_id="a2", template_id="t1", action="approve",
                actor_open_id="admin_1", reason=None)
    session.commit()
    out = repo.list_by_template("t1")
    assert len(out) == 2


def test_insert_with_reason(session):
    repo = TemplateAuditRepo(session)
    repo.insert(audit_id="a3", template_id="t1", action="reject",
                actor_open_id="admin_1", reason="内容不完整")
    session.commit()
    out = repo.list_by_template("t1")
    assert out[0].reason == "内容不完整"