"""Phase 8 T7: TagService + FavoriteService 单元测试（SQLite 真库）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.favorite_service import FavoriteService
from orchestrator.templates.tag_service import TagService
from persistence.models import Base
from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_repo import TemplateRepo
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


@pytest.fixture
def tag_svc(session):
    return TagService(TemplateTagRepo(session), TemplateRepo(session))


@pytest.fixture
def fav_svc(session):
    return FavoriteService(TemplateFavoriteRepo(session), TemplateRepo(session))


def _tpl(session, tid="t1", owner="ou_1"):
    TemplateRepo(session).upsert(
        template_id=tid, owner_open_id=owner, name="n",
        type_="block", description="",
    )


def test_tag_attach_non_owner_permission_error(session, tag_svc):
    _tpl(session)
    with pytest.raises(PermissionError):
        tag_svc.attach(template_id="t1", tag="bio", caller_open_id="ou_2")


def test_tag_attach_normalizes_and_lists(session, tag_svc):
    _tpl(session)
    tag_svc.attach(template_id="t1", tag="  BLAST ", caller_open_id="ou_1")
    tag_svc.attach(template_id="t1", tag="blast", caller_open_id="ou_1")  # 幂等
    assert tag_svc.list_tags("t1") == ["blast"]


def test_tag_find_by_tag_filters_archived(session, tag_svc):
    _tpl(session, tid="t1")
    _tpl(session, tid="t2")
    tag_svc.attach(template_id="t1", tag="bio", caller_open_id="ou_1")
    tag_svc.attach(template_id="t2", tag="bio", caller_open_id="ou_1")
    TemplateRepo(session).delete("t2")  # 打标后归档
    hits = tag_svc.find_by_tag("bio")
    assert [t.template_id for t in hits] == ["t1"]


def test_favorite_and_list(session, fav_svc):
    _tpl(session, tid="t1")
    _tpl(session, tid="t2")
    fav_svc.favorite(template_id="t1", caller_open_id="ou_1")
    fav_svc.favorite(template_id="t2", caller_open_id="ou_1")
    hits = fav_svc.list_favorites("ou_1")
    assert sorted(t.template_id for t in hits) == ["t1", "t2"]


def test_unfavorite_removes(session, fav_svc):
    _tpl(session)
    fav_svc.favorite(template_id="t1", caller_open_id="ou_1")
    fav_svc.unfavorite(template_id="t1", caller_open_id="ou_1")
    fav_svc.unfavorite(template_id="t1", caller_open_id="ou_1")  # 幂等
    assert fav_svc.list_favorites("ou_1") == []


def test_favorite_missing_template_raises(session, fav_svc):
    with pytest.raises(ValueError):
        fav_svc.favorite(template_id="missing", caller_open_id="ou_1")
