"""SQLAlchemy engine + session factory。

engine 与 SessionLocal 在首次调用 get_engine() 时懒加载，
避免 import 时就要求 DATABASE_URL 可用，方便测试覆盖。
"""
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def _init_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    from config.settings import load_settings
    settings = load_settings()
    _engine = create_engine(settings.database_url,
                            **_engine_kwargs(settings.database_url))
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    """按方言组装 create_engine 参数（pg/sqlite 分叉的唯一出口）。

    pg：pool_size/max_overflow 仅对 QueuePool 合法（sqlite 传了 TypeError）；
    connect_timeout=10 防黑端口挂死——2026-09-09 真机定位：pg 容器停止时
    psycopg 无显式超时纯靠 OS TCP 重传约 130s 才报错，启动期 DB 调用
    （ModelSwitchService 恢复 active 模型等）把 ws_client 启动拖住两分钟。
    sqlite：connect_args 对 SingletonThreadPool 无意义，一律不传。
    """
    if database_url.startswith("sqlite"):
        return {"pool_pre_ping": True, "future": True}
    return {
        "pool_pre_ping": True,
        "future": True,
        "pool_size": 5,
        "max_overflow": 10,
        "connect_args": {"connect_timeout": 10},
    }
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    """获取全局 Engine，首次调用时从 settings 初始化。"""
    return _init_engine()


def session_factory() -> sessionmaker[Session]:
    """获取全局 sessionmaker。"""
    _init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def configure_engine(database_url: str) -> None:
    """测试或运行时替换 engine（方言参数与主路径同出口，pg 同样带超时）。"""
    global _engine, _SessionLocal
    _engine = create_engine(database_url, **_engine_kwargs(database_url))
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务作用域：with 块内任何异常触发 rollback。"""
    factory = session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
