"""Phase 8 T3: TagRepo + FavoriteRepo 单元测试（SQLite 真库）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_tag_repo import TemplateTagRepo


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


def test_tag_add_idempotent(session):
    repo = TemplateTagRepo(session)
    repo.add(tag_id="g1", template_id="t1", tag="blast", created_by="ou_1")
    repo.add(tag_id="g2", template_id="t1", tag="blast", created_by="ou_1")
    assert repo.list_by_template("t1") == ["blast"]


def test_tag_remove_and_missing_silent(session):
    repo = TemplateTagRepo(session)
    repo.add(tag_id="g1", template_id="t1", tag="a", created_by="ou_1")
    repo.remove(template_id="t1", tag="a")
    repo.remove(template_id="t1", tag="not-exist")  # 静默
    assert repo.list_by_template("t1") == []


def test_tag_find_by_tag(session):
    repo = TemplateTagRepo(session)
    repo.add(tag_id="g1", template_id="t1", tag="bio", created_by="ou_1")
    repo.add(tag_id="g2", template_id="t2", tag="bio", created_by="ou_2")
    repo.add(tag_id="g3", template_id="t3", tag="chem", created_by="ou_1")
    assert sorted(repo.find_template_ids_by_tag("bio")) == ["t1", "t2"]


def test_favorite_add_idempotent(session):
    repo = TemplateFavoriteRepo(session)
    repo.add(favorite_id="f1", template_id="t1", user_open_id="ou_1")
    repo.add(favorite_id="f2", template_id="t1", user_open_id="ou_1")
    assert repo.list_by_user("ou_1") == ["t1"]


def test_favorite_remove(session):
    repo = TemplateFavoriteRepo(session)
    repo.add(favorite_id="f1", template_id="t1", user_open_id="ou_1")
    repo.remove(template_id="t1", user_open_id="ou_1")
    repo.remove(template_id="t1", user_open_id="ou_1")  # 幂等
    assert repo.list_by_user("ou_1") == []


def test_favorite_list_by_user_multiple(session):
    repo = TemplateFavoriteRepo(session)
    repo.add(favorite_id="f1", template_id="t1", user_open_id="ou_1")
    repo.add(favorite_id="f2", template_id="t2", user_open_id="ou_1")
    repo.add(favorite_id="f3", template_id="t3", user_open_id="ou_2")
    assert sorted(repo.list_by_user("ou_1")) == ["t1", "t2"]
    assert repo.list_by_user("ou_2") == ["t3"]
