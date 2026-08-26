"""Phase 8 T5: CommentActionService 单元测试。"""
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.comment_action_service import (
    CommentActionService,
    parse_action,
)
from persistence.models import Base
from persistence.repositories.comment_repo import CommentRepo
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


def _setup(session, *, type_="subplan", owner="ou_owner"):
    """建 subplan 模板 t1 + 评论 c1，返回 service。"""
    TemplateRepo(session).upsert(
        template_id="t1", owner_open_id=owner, name="std",
        type_=type_, steps_json="[]", description="原始描述",
    )
    version_service = MagicMock()
    svc = CommentActionService(
        CommentRepo(session), TemplateRepo(session), version_service,
    )
    return svc, version_service


def _comment(session, cid, text):
    CommentRepo(session).upsert_one(
        comment_id=cid, doc_id="d1", user_name="导师", text=text,
    )


# --- parse_action ---

def test_parse_action_three_prefixes():
    a = parse_action("/replan t1 增加对照组")
    assert (a.kind, a.template_id, a.payload) == ("replan", "t1", "增加对照组")
    b = parse_action("/revise t1 新描述文本")
    assert (b.kind, b.template_id, b.payload) == ("revise", "t1", "新描述文本")
    c = parse_action("/add-step t1 blast_local")
    assert (c.kind, c.template_id, c.payload) == ("add-step", "t1", "blast_local")


def test_parse_action_plain_text_returns_none():
    assert parse_action("写得不错") is None
    assert parse_action("建议 /replan 一下") is None  # 非前缀开头
    assert parse_action("/replan t1") is None         # 参数不足
    assert parse_action("") is None


# --- apply ---

def test_apply_replan_appends_description_and_marks_processed(session):
    svc, vs = _setup(session)
    _comment(session, "c1", "/replan t1 增加对照组")
    out = svc.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out["applied"] == 1
    tpl = TemplateRepo(session).get("t1")
    assert "[replan by ou_owner] 增加对照组" in tpl.description
    assert "原始描述" in tpl.description
    vs.on_template_upsert.assert_called_once()
    rows = CommentRepo(session).list_by_doc("d1")
    assert rows[0].processed_at is not None


def test_apply_revise_overwrites_description(session):
    svc, _ = _setup(session)
    _comment(session, "c1", "/revise t1 全新描述")
    out = svc.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out["applied"] == 1
    tpl = TemplateRepo(session).get("t1")
    assert tpl.description == "全新描述"
    assert "原始描述" not in tpl.description


def test_apply_add_step_appends_step(session):
    svc, _ = _setup(session)
    _comment(session, "c1", "/add-step t1 blast_local")
    out = svc.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out["applied"] == 1
    tpl = TemplateRepo(session).get("t1")
    steps = json.loads(tpl.steps_json)
    assert steps == [{"tool": "blast_local", "args": {}}]


def test_apply_non_owner_fails_without_mutation(session):
    svc, _ = _setup(session)
    _comment(session, "c1", "/replan t1 越权修改")
    out = svc.apply(doc_id="d1", caller_open_id="ou_attacker")
    assert out["failed"] == 1
    assert out["details"][0]["reason"] == "not_owner"
    tpl = TemplateRepo(session).get("t1")
    assert tpl.description == "原始描述"


def test_apply_add_step_to_block_template_fails(session):
    svc, _ = _setup(session, type_="block")
    _comment(session, "c1", "/add-step t1 blast_local")
    out = svc.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out["failed"] == 1
    assert out["details"][0]["reason"] == "not_subplan"


def test_apply_skips_plain_comments(session):
    svc, _ = _setup(session)
    _comment(session, "c1", "写得不错")
    out = svc.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out == {"applied": 0, "skipped": 1, "failed": 0,
                   "details": [{"comment_id": "c1", "kind": None,
                                "status": "skipped"}]}
    # 普通评论不打标
    rows = CommentRepo(session).list_by_doc("d1")
    assert rows[0].processed_at is None
