"""数据库连接与会话管理。

这里用的是 SQLite + SQLAlchemy 2.0。为什么先用 SQLite：
- 零安装，clone 下来就能跑，演示友好；
- SQLAlchemy 是 ORM，同一套代码把 FCL_DB_URL 换成 mysql+pymysql://... 就能连 MySQL，
  业务代码一行都不用改。这就是「用 ORM 屏蔽数据库方言」的实际价值。
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _build_engine():
    url = settings.db_url
    connect_args = {}
    if url.startswith("sqlite"):
        # SQLite 默认禁止跨线程复用连接，FastAPI 是多线程/多协程的，必须关掉这个检查
        connect_args = {"check_same_thread": False}
        db_path = url.replace("sqlite:///", "")
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)
    if url.startswith("sqlite"):
        # 打开 WAL：读写可以并发，后台 worker 写库时不会把 API 读请求堵死
        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """建表。生产环境会换成 Alembic 迁移，这里为了 24 小时交付从简。"""
    from . import models  # noqa: F401  确保模型已注册到 Base.metadata

    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Session:
    """上下文管理器的会话：正常提交，异常回滚，最后一定关闭。

    为什么要包一层：手写 try/except/finally 到处漏，事务边界一乱就出现「数据写一半」。
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
