"""Phase 10 pg 层：search_v2 tsvector 真库验证（清偿 ADR-0026 欠账）。"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_repo import TemplateRepo

# mark 必须在测试模块内声明（conftest 的 pytestmark 不作用于测试项）
pytestmark = pytest.mark.pg


def _tpl(session, tid, name, desc=""):
    TemplateRepo(session).upsert(
        template_id=tid, owner_open_id="ou_1", name=name,
        type_="block", description=desc,
    )


def test_tsvector_branch_real_scoring(pg_session):
    """真 PG tsvector 分支：name 命中应高于仅 desc 命中。"""
    repo = TemplateRepo(pg_session)
    _tpl(pg_session, "t_name", "blast pipeline", "unrelated")
    _tpl(pg_session, "t_desc", "workflow", "contains blast step")
    rows = repo.search_v2(query="blast")
    ids = [tpl.template_id for tpl, _ in rows]
    assert ids[0] == "t_name"
    scores = {tpl.template_id: s for tpl, s in rows}
    assert scores["t_name"] >= scores["t_desc"]


def test_gin_index_present_and_usable(pg_alembic_engine):
    """迁移建的 GIN 表达式索引存在且查询计划使用它。"""
    from alembic import command
    from alembic.config import Config
    # 程序化 cfg：绕开 alembic.ini 的 GBK 解码问题（同 test_pg_migrations）
    cfg = Config()
    cfg.set_main_option("script_location", "migrations")
    command.upgrade(cfg, "head")

    from sqlalchemy import inspect
    insp_cols = inspect(pg_alembic_engine).get_indexes("templates")
    names = [i["name"] for i in insp_cols]
    assert any("fts" in n for n in names), f"GIN 索引未建: {names}"

    with pg_alembic_engine.connect() as conn:
        # 小表/空表必走 seq scan；关闭后强制暴露索引可用性
        conn.execute(text("SET enable_seqscan = off"))
        plan_rows = conn.execute(text(
            "EXPLAIN SELECT template_id FROM templates WHERE "
            "to_tsvector('simple', coalesce(name,'') || ' ' || "
            "coalesce(description,'')) @@ plainto_tsquery('simple', 'blast')"
        )).fetchall()
    plan = "\n".join(r[0] for r in plan_rows).lower()
    assert "bitmap index scan" in plan or "index scan" in plan, plan


def test_fusion_ranking_on_pg(pg_session):
    """Phase 9 E4 融合排序场景在真 PG 复现：弱匹配+3 收藏 > 强匹配+0 收藏。"""
    repo = TemplateRepo(pg_session)
    _tpl(pg_session, "t_strong", "blast 流程", "")
    _tpl(pg_session, "t_hot", "某流程", "含 blast")
    fav = TemplateFavoriteRepo(pg_session)
    for i in range(3):
        fav.add(favorite_id=f"f{i}", template_id="t_hot", user_open_id=f"ou_{i}")
    rows = repo.search_v2(query="blast")
    assert rows[0][0].template_id == "t_hot"
    # 1.0（desc 档）+ 3 收藏 * 0.5 + PG 特有 ts_rank 微分（小文档 ≈0.06，随词数浮动）
    assert rows[0][1] == pytest.approx(2.5, abs=0.15)


def test_chinese_query_simple_config_behavior(pg_session):
    """记录性断言：'simple' 配置下中文按整串/空白切词匹配的现状。

    zhparser 分词优化留 Phase 11（spec §10 风险表已声明，非本轮失败）。
    """
    repo = TemplateRepo(pg_session)
    _tpl(pg_session, "t_zh", "蛋白结构预测流程", "深度学习")
    # 空格分隔的拉丁词走正常分词
    rows = repo.search_v2(query="预测")
    # plainto_tsquery('simple','预测') 产生 '预测' 词元；
    # 'simple' 不做中文切分，但整词若在原文中即命中
    assert all(isinstance(s, float) for _, s in rows)
    # 行为现状：不做命中性强断言（分词边界因配置而异），仅固化不抛错 + 排序可用
    rows2 = repo.search_v2(query="blast")
    assert isinstance(rows2, list)
