"""Phase 10: pg 层测试夹具（-m pg 专属，ADR-0029）。

无 PG_TEST_URL 或连接失败时整层自动 skip（不算失败）。
每测试函数独立事务回滚隔离，避免状态串扰。
"""
from __future__ import annotations

import os

import pytest

# +psycopg 显式指定 psycopg3 方言（裸 postgresql:// 会找未安装的 psycopg2）
DEFAULT_URL = "postgresql+psycopg://agent:agent@localhost:5433/agent"


def _try_connect(url: str):
    try:
        import psycopg
        # libpq 只认 postgresql://（无 +psycopg），SQLAlchemy 侧才需要方言前缀
        raw = url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(raw, connect_timeout=3):
            return True
    except Exception:
        return False


def _pg_url() -> str | None:
    url = os.environ.get("PG_TEST_URL", DEFAULT_URL)
    if not _try_connect(url):
        return None
    return url


@pytest.fixture(scope="session")
def pg_url():
    url = _pg_url()
    if url is None:
        pytest.skip("PG 不可用：docker compose up -d postgres 后重试（-m pg 层）")
    return url


@pytest.fixture()
def pg_session(pg_url):
    """每测试独立 schema 级隔离：Base.metadata 建全表，测试后 drop。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(pg_url)
    from persistence.models import Base
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def pg_alembic_engine(pg_url, monkeypatch):
    """迁移测试用：每测试清库从零跑 alembic，并让 env.py 的
    load_settings() 指向本测试库（monkeypatch 测试后自动还原）。"""
    from sqlalchemy import create_engine, text
    # env.py 强制走 load_settings()：注入 DATABASE_URL 与其依赖的
    # 飞书/LLM 环境变量（迁移本身不用这些值，仅求 settings 可构造）
    monkeypatch.setenv("DATABASE_URL", pg_url)
    for key in (
        "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WEBHOOK_SECRET",
        "LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
        "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL",
    ):
        monkeypatch.setenv(key, f"pg-test-{key.lower()}")
    engine = create_engine(pg_url, isolation_level="AUTOCOMMIT")
    # 清库保证迁移从零开始
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()
