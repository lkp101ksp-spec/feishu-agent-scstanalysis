"""persistence.engine 单测：pg 方言必须带 connect_timeout（wedge 类根治）。

2026-09-09 真机定位：ModelSwitchService 启动期 DB 调用在 pg 黑端口下阻塞
130s（psycopg 无显式超时，纯靠 OS TCP 重传）——异常最终被吞进程不死，
但启动/请求被拖住。统一在 engine 创建处给 pg 方言接 connect_timeout=10，
sqlite 非法参数不传（Phase 10 已有同款分支）。
"""
from unittest.mock import patch

import persistence.engine as engine_mod


def _init_with_url(database_url: str):
    """以指定 DATABASE_URL 重建全局 engine，返回 create_engine 收到的 kwargs。"""
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        from sqlalchemy import create_engine as real_create_engine
        return real_create_engine("sqlite:///:memory:")

    engine_mod._engine = None
    engine_mod._SessionLocal = None
    try:
        with patch.object(engine_mod, "create_engine", fake_create_engine):
            with patch("config.settings.load_settings") as ls:
                ls.return_value.database_url = database_url
                engine_mod._init_engine()
    finally:
        engine_mod._engine = None
        engine_mod._SessionLocal = None
    return captured


def test_pg_engine_has_connect_timeout():
    """pg URL：create_engine 必须收到 connect_args.connect_timeout（防黑端口挂死）。"""
    kw = _init_with_url("postgresql+psycopg://agent:agent@localhost:5433/agent")
    assert kw["connect_args"]["connect_timeout"] == 10
    assert kw["pool_pre_ping"] is True
    assert kw["pool_size"] == 5  # 既有 pg 池参数不回归


def test_sqlite_engine_no_connect_timeout_no_pool_kwargs():
    """sqlite：不传 connect_timeout/pool_size（SingletonThreadPool 非法参数）。"""
    kw = _init_with_url("sqlite:///:memory:")
    assert "connect_args" not in kw
    assert "pool_size" not in kw
    assert kw["pool_pre_ping"] is True


def test_configure_engine_pg_also_bounded():
    """测试/运行时替换入口 configure_engine 同样带 pg 超时（不走漏）。"""
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        from sqlalchemy import create_engine as real_create_engine
        return real_create_engine("sqlite:///:memory:")

    try:
        with patch.object(engine_mod, "create_engine", fake_create_engine):
            engine_mod.configure_engine(
                "postgresql+psycopg://agent:agent@localhost:5433/agent")
    finally:
        engine_mod._engine = None
        engine_mod._SessionLocal = None
    assert captured["connect_args"]["connect_timeout"] == 10
