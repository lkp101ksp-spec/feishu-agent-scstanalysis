"""Phase 10 pg 层：0002 迁移真库验证（ADR-0029 清偿迁移债）。"""
from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from persistence.models import Base

pytestmark = pytest.mark.pg

EXPECTED_TABLES = set(Base.metadata.tables.keys())


def _alembic_cfg() -> Config:
    """程序化构造：绕开 alembic.ini（Windows 下 alembic 按 locale=GBK
    读含中文注释的 ini 会 UnicodeDecodeError）；URL 由 env.py 从
    DATABASE_URL 环境变量注入（见 conftest.pg_alembic_engine）。"""
    cfg = Config()
    cfg.set_main_option("script_location", "migrations")
    return cfg


def test_0002_upgrade_creates_all_orm_tables(pg_alembic_engine):
    """upgrade head 后：17 张表齐，且与 Base.metadata 表名集合一致。"""
    command.upgrade(_alembic_cfg(), "head")
    insp = inspect(pg_alembic_engine)
    got = set(insp.get_table_names()) - {"alembic_version"}
    assert got == EXPECTED_TABLES, f"缺失: {EXPECTED_TABLES - got}; 多余: {got - EXPECTED_TABLES}"


def test_downgrade_to_base_then_reupgrade(pg_alembic_engine):
    """downgrade 到 0001 基线仅存 Phase 1 六表；可再次 upgrade。"""
    command.upgrade(_alembic_cfg(), "head")
    command.downgrade(_alembic_cfg(), "0001_init")
    insp = inspect(pg_alembic_engine)
    got = set(insp.get_table_names()) - {"alembic_version"}
    phase1 = {
        "sessions", "tasks", "artifacts", "doc_writes",
        "audit_logs", "idempotency_keys",
    }
    assert got == phase1, f"回退基线不符: {got}"
    # 幂等可重放
    command.upgrade(_alembic_cfg(), "head")
    insp = inspect(pg_alembic_engine)
    got = set(insp.get_table_names()) - {"alembic_version"}
    assert got == EXPECTED_TABLES
