"""SQLAlchemy engine + session factory。

engine 与 SessionLocal 在首次调用 get_engine() 时懒加载，
避免 import 时就要求 DATABASE_URL 可用，方便测试覆盖。
"""
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _init_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    from config.settings import load_settings
    settings = load_settings()
    _engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    """获取全局 Engine，首次调用时从 settings 初始化。"""
    return _init_engine()


def session_factory() -> sessionmaker:
    """获取全局 sessionmaker。"""
    _init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def configure_engine(database_url: str) -> None:
    """测试或运行时替换 engine。"""
    global _engine, _SessionLocal
    _engine = create_engine(database_url, pool_pre_ping=True, future=True)
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