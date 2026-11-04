"""幂等（§3.1）。

语义：
- 写接口缺 ``Idempotency-Key``：400；
- 同键同参：回放第一次的结果，响应带 ``Idempotent-Replayed: true``；
- 同键异参（方法、路径、body 任一不同）：422；
- 同键并发（第一次还没做完）：409；
- 记录保留 24 个仿真小时，之后同一个键视为新请求。

存储表放在各服务自己的库里（``idempotency_record``）。业务写入与“完成”标记在同一个事务里提交，
所以不会出现“业务成功了但键没记上”。第一次请求失败（抛异常）时标记会被撤销，客户端可以安全重试。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import BigInteger, Integer, String, Text, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from . import clock, fmt
from .db import SimBase
from .errors import ApiError

IDEMPOTENCY_TTL_MS = 24 * 3600 * 1000  # 24 仿真小时（基础设施参数，不是业务规则）
REPLAY_HEADER = "Idempotent-Replayed"


class IdempotencyRecord(SimBase):
    __tablename__ = "idempotency_record"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))  # in_progress / done
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)


def idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", description="写接口必填的幂等键"),
) -> str:
    """FastAPI 依赖：取出幂等键，缺失返回 400。"""
    if not idempotency_key or not idempotency_key.strip():
        raise ApiError(400, "IDEM_MISSING", "写接口必须带 Idempotency-Key 请求头")
    return idempotency_key.strip()


def fingerprint(method: str, path: str, payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{method.upper()}\n{path}\n{canonical}".encode()).hexdigest()


class IdempotencyStore:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def begin(self, key: str, fp: str) -> tuple[int, str] | None:
        """登记一个新请求。

        返回 None 表示这是首次请求（已写入 in_progress 标记）；
        返回 (status, body_json) 表示应当回放；其余情况抛 ApiError。
        """
        now_ms = fmt.epoch_ms(clock.now())
        for _ in range(2):
            with self.factory() as s:
                rec = s.get(IdempotencyRecord, key)
                if rec is not None and rec.expires_at_ms <= now_ms:
                    s.delete(rec)
                    s.commit()
                    rec = None
                if rec is None:
                    s.add(
                        IdempotencyRecord(
                            key=key, fingerprint=fp, state="in_progress",
                            created_at_ms=now_ms, expires_at_ms=now_ms + IDEMPOTENCY_TTL_MS,
                        )
                    )
                    try:
                        s.commit()
                        return None
                    except IntegrityError:  # 并发时有人抢先插入，回到循环重新读取
                        s.rollback()
                        continue
                if rec.fingerprint != fp:
                    raise ApiError(422, "IDEM_REUSED", "同一个 Idempotency-Key 已用于不同的请求参数")
                if rec.state == "done":
                    return rec.response_status or 200, rec.response_body or "null"
                raise ApiError(409, "IDEM_IN_PROGRESS", "相同 Idempotency-Key 的请求仍在处理中")
        raise ApiError(409, "IDEM_IN_PROGRESS", "相同 Idempotency-Key 的请求仍在处理中")

    def finish(self, db: Session, key: str, status: int, body: Any) -> None:
        """在业务事务内标记完成（随业务一起提交）。"""
        rec = db.get(IdempotencyRecord, key)
        rec.state = "done"
        rec.response_status = status
        rec.response_body = json.dumps(body, ensure_ascii=False, default=str)

    def abort(self, key: str) -> None:
        """首次请求失败：撤销标记，允许重试。"""
        with self.factory() as s:
            s.execute(delete(IdempotencyRecord).where(IdempotencyRecord.key == key))
            s.commit()


def run_idempotent(
    request: Request,
    db: Session,
    key: str,
    payload: Any,
    work: Callable[[Session], tuple[int, Any]],
) -> JSONResponse:
    """执行一个幂等写操作。

    work(db) 返回 (HTTP 状态, 成功数据)；不要在里面 commit，这里会统一提交。
    成功数据会按本服务的风格包进包络，并整体保存用于回放。
    """
    sim = request.app.state.sim
    store: IdempotencyStore = sim.idem_store
    fp = fingerprint(request.method, request.url.path, payload)
    replay = store.begin(key, fp)
    if replay is not None:
        status, body = replay
        return JSONResponse(json.loads(body), status_code=status, headers={REPLAY_HEADER: "true"})
    try:
        status, data = work(db)
        body = jsonable_encoder(sim.style.wrap(data, getattr(request.state, "request_id", "")))
        store.finish(db, key, status, body)
        db.commit()
    except BaseException:
        db.rollback()
        store.abort(key)
        raise
    return JSONResponse(body, status_code=status)
