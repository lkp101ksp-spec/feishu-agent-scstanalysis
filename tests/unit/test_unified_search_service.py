"""Phase 9 T3/T6: 融合检索单元测试（SQLite 真库，方言降级路径）。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.unified_search_service import UnifiedSearchService
from persistence.models import Base
from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_repo import TemplateRepo, _is_postgres
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
def svc(session):
    return UnifiedSearchService(
        TemplateRepo(session),
        tag_repo=TemplateTagRepo(session),
        favorite_repo=TemplateFavoriteRepo(session),
    )


def _tpl(session, tid, name, desc="", scope="user"):
    TemplateRepo(session).upsert(
        template_id=tid, owner_open_id="ou_1", name=name,
        type_="block", description=desc, scope=scope,
    )


def test_name_match_ranks_higher(session, svc):
    _tpl(session, "t_name", "blast pipeline", "其他内容")
    _tpl(session, "t_desc", "流程", "包含 blast 步骤")
    out = svc.search(query="blast")
    ids = [r["template_id"] for r in out]
    assert ids[0] == "t_name"
    assert out[0]["score"] > out[1]["score"]


def test_tag_filter(session, svc):
    _tpl(session, "t1", "流程 A")
    _tpl(session, "t2", "流程 B")
    TemplateTagRepo(session).add(
        tag_id="g1", template_id="t1", tag="bio", created_by="ou_1")
    out = svc.search(query="流程", tag="bio")
    assert [r["template_id"] for r in out] == ["t1"]


def test_favorite_boost(session, svc):
    _tpl(session, "t_strong", "blast 流程", "")      # name 命中 = 2.0
    _tpl(session, "t_hot", "某流程", "含 blast")      # 仅 desc = 1.0
    fav = TemplateFavoriteRepo(session)
    for i in range(3):
        fav.add(favorite_id=f"f{i}", template_id="t_hot",
                user_open_id=f"ou_{i}")
    out = svc.search(query="blast")
    # t_hot: 1.0 + 3*0.5 = 2.5 > t_strong 2.0
    assert out[0]["template_id"] == "t_hot"
    assert out[0]["favorite_count"] == 3


def test_scope_filter(session, svc):
    _tpl(session, "t_pub", "流程", scope="public")
    _tpl(session, "t_priv", "流程", scope="user")
    out = svc.search(query="流程", scope="public")
    assert [r["template_id"] for r in out] == ["t_pub"]


def test_empty_query_hotness_order(session, svc):
    _tpl(session, "t_cold", "冷门")
    _tpl(session, "t_hot", "热门")
    fav = TemplateFavoriteRepo(session)
    fav.add(favorite_id="f1", template_id="t_hot", user_open_id="ou_1")
    out = svc.search(query="")
    assert out[0]["template_id"] == "t_hot"


def test_archived_excluded(session, svc):
    _tpl(session, "t1", "流程")
    TemplateRepo(session).delete("t1")
    assert svc.search(query="流程") == []


def test_is_postgres_detection():
    for name, expected in [("postgresql", True), ("sqlite", False)]:
        s = MagicMock()
        s.bind.dialect.name = name
        assert _is_postgres(s) is expected


def test_service_formats_results(session, svc):
    _tpl(session, "t1", "流程 A")
    TemplateTagRepo(session).add(
        tag_id="g1", template_id="t1", tag="bio", created_by="ou_1")
    out = svc.search(query="流程")
    assert set(out[0].keys()) == {
        "template_id", "name", "score", "tags", "favorite_count"}
    assert out[0]["tags"] == ["bio"]


def test_service_tag_normalized(session, svc):
    _tpl(session, "t1", "流程")
    TemplateTagRepo(session).add(
        tag_id="g1", template_id="t1", tag="bio", created_by="ou_1")
    out = svc.search(query="流程", tag="  BIO ")
    assert [r["template_id"] for r in out] == ["t1"]
