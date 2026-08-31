"""Phase 19: 标签推荐单测（共现 + 热度兜底 + 排除已有，spec §2.2）。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.tag_recommend_service import TagRecommendService
from persistence.models import Base
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


def _tag(tpl: str, tag: str) -> dict:
    return {"template_id": tpl, "tag": tag}


class _StubRepo:
    """list_all stub：接受 (template_id, tag) 元组列表。"""

    def __init__(self, pairs):
        self.pairs = pairs

    def list_all(self):
        return self.pairs


def _tpl_repo(archived=False):
    repo = MagicMock()
    tpl = MagicMock(archived_at=None if not archived else "2026-09-01")
    repo.get.return_value = tpl
    return repo


def test_suggest_prefers_cooccurring_tags():
    """与已有标签同模板出现的标签优先，按共现分排序。"""
    pairs = [
        ("t1", "pcr"), ("t1", "rna"),           # 目标模板已有 pcr, rna
        ("t2", "pcr"), ("t2", "qpcr"), ("t2", "primer"),
        ("t3", "rna"), ("t3", "qpcr"),           # qpcr 共现 2 次 > primer 1 次
        ("t4", "unrelated"),                     # 无交集不贡献
    ]
    svc = TagRecommendService(_StubRepo(pairs), _tpl_repo())
    out = svc.suggest(template_id="t1")
    assert out[0] == "qpcr"      # 共现 2（pcr + rna 各贡献）
    assert "primer" in out       # 共现 1
    assert "unrelated" not in out or out.index("unrelated") > 1  # 热度兜底排后
    assert "pcr" not in out and "rna" not in out  # 排除已有


def test_suggest_hotness_fallback_when_no_cooc():
    """无共现时用全站热度 top 补齐。"""
    pairs = [
        ("t1", "pcr"),
        ("t2", "bioinfo"), ("t2", "blast"),
        ("t3", "bioinfo"), ("t3", "rna"),
        ("t4", "bioinfo"),                      # bioinfo 热度 3 最高
    ]
    svc = TagRecommendService(_StubRepo(pairs), _tpl_repo())
    out = svc.suggest(template_id="t1", limit=2)
    assert out[0] == "bioinfo"  # 热度最高优先
    assert len(out) == 2
    assert "pcr" not in out


def test_suggest_respects_limit():
    """limit 截断推荐数量。"""
    pairs = [("t1", "a")] + [("t2", "a")] + [
        ("t2", f"tag{i}") for i in range(10)
    ]
    svc = TagRecommendService(_StubRepo(pairs), _tpl_repo())
    assert len(svc.suggest(template_id="t1", limit=3)) == 3


def test_suggest_template_not_found_raises():
    """模板不存在/归档抛 ValueError（调用方转 404）。"""
    repo = MagicMock()
    repo.get.return_value = None
    svc = TagRecommendService(_StubRepo([]), repo)
    with pytest.raises(ValueError, match="not found"):
        svc.suggest(template_id="missing")


def test_suggest_archived_template_raises():
    """已归档模板同样 404。"""
    svc = TagRecommendService(_StubRepo([]), _tpl_repo(archived=True))
    with pytest.raises(ValueError, match="not found"):
        svc.suggest(template_id="t1")


def test_suggest_new_template_without_tags():
    """无任何标签的新模板：纯热度推荐（排除空）。"""
    pairs = [("t2", "x"), ("t3", "x"), ("t4", "y")]
    svc = TagRecommendService(_StubRepo(pairs), _tpl_repo())
    out = svc.suggest(template_id="t1", limit=5)
    assert out[0] == "x"  # 热度 2
    assert "y" in out


def test_repo_list_all_roundtrip(session):
    """TemplateTagRepo.list_all 返回全部 (template_id, tag) 行。"""
    repo = TemplateTagRepo(session)
    repo.add(tag_id="id1", template_id="t1", tag="pcr", created_by="u")
    repo.add(tag_id="id2", template_id="t1", tag="rna", created_by="u")
    repo.add(tag_id="id3", template_id="t2", tag="pcr", created_by="u")
    assert sorted(repo.list_all()) == [
        ("t1", "pcr"), ("t1", "rna"), ("t2", "pcr")]
