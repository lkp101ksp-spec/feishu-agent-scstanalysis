import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.artifact_repo import ArtifactRepo


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


def test_create_and_update_storage(session):
    repo = ArtifactRepo(session)
    aid = repo.create(task_id="t1", kind="image", storage_type="pending")
    assert repo.get(aid).storage_type == "pending"
    repo.update_storage(
        aid,
        storage_type="drive",
        storage_ref="boxcn_xxx",
        file_token="boxcn_xxx",
        drive_url="https://...",
        mime="image/png",
        size_bytes=12345,
        sha256="abc",
        caption="图1",
    )
    a = repo.get(aid)
    assert a.storage_type == "drive"
    assert a.file_token == "boxcn_xxx"
    assert a.mime == "image/png"
