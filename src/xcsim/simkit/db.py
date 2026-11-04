"""SQLAlchemy 基础：每服务一库、WAL、从快照恢复。

路由用同步 ``def``（M7），所以这里全是同步 SQLAlchemy。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import db_path


class SimBase(DeclarativeBase):
    """simkit 自己的表（幂等记录）用这个基类；业务表由各服务另建基类。"""


def create_sqlite_engine(path: str | Path, *, echo: bool = False) -> Engine:
    """创建 SQLite 引擎：WAL、外键、忙等待 5 秒，允许跨线程使用（FastAPI 线程池）。"""
    p = Path(path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{p.as_posix()}", echo=echo, connect_args={"check_same_thread": False, "timeout": 5}
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def service_engine(service: str) -> Engine:
    """某服务的默认库（data/<service>.db）。"""
    return create_sqlite_engine(db_path(service))


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def db_dependency(factory: sessionmaker[Session]) -> Callable[[], Iterator[Session]]:
    """生成 FastAPI 依赖：一个请求一个会话，请求结束关闭。"""

    def get_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    return get_db


def restore_from_snapshot(engine: Engine, snapshot: str | Path) -> None:
    """用 SQLite backup API 把快照恢复进现有库（F7）。

    Windows 下库文件有句柄时不能直接覆盖文件，所以：先 dispose 引擎释放连接池，
    再用 backup 把快照页面写进目标库。
    """
    snap = Path(snapshot)
    if not snap.exists():
        raise FileNotFoundError(snap)
    target = engine.url.database
    engine.dispose()
    src = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target, timeout=5)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def ensure_sim_tables(engine: Engine) -> None:
    """建 simkit 自己的表（幂等记录）。服务启动或测试夹具里调用。"""
    from . import idempotency  # noqa: F401  确保模型已注册

    SimBase.metadata.create_all(engine)
