"""simkit 测试公用：为每种错误风格搭一个带真实 SQLite 的迷你服务。"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from xcsim.simkit.auth import require
from xcsim.simkit.client import TokenAuth
from xcsim.simkit.config import get_settings
from xcsim.simkit.db import SimBase, create_sqlite_engine, db_dependency, ensure_sim_tables, make_session_factory
from xcsim.simkit.errors import ApiError
from xcsim.simkit.idempotency import idempotency_key, run_idempotent
from xcsim.simkit.service import build_app

# 8 个服务 = 7 种风格（control 沿用 ticket 风格）
SERVICES = ["catalog", "order", "aftersale", "logistics", "payment", "member", "ticket", "control"]
# 响应里“数据”所在的前缀（包络不同）
DATA_PREFIX = {
    "catalog": "", "ticket": "", "payment": "", "control": "",
    "order": "data.", "aftersale": "data.", "logistics": "data.", "member": "result.",
}


class Note(SimBase):
    __tablename__ = "test_note"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(100))


def kind_for(service: str) -> str:
    return "control" if service == "control" else "cs-agent"


def scope_of(service: str, action: str) -> str:
    return "control:admin" if service == "control" else f"{service}:{action}"


class Harness:
    """迷你服务 + 观测点。"""

    def __init__(self, service: str, tmp_path: Path, seed: int = 42) -> None:
        self.service = service
        self.engine = create_sqlite_engine(tmp_path / f"{service}.db")
        ensure_sim_tables(self.engine)
        self.factory = make_session_factory(self.engine)
        self.hits = 0
        self.slow_started = threading.Event()
        self.slow_release = threading.Event()
        self.fail_next = False
        self.ticks: list = []
        self.events: list = []
        self.resets: list = []

        router = APIRouter(prefix=f"/{service}/v1")
        get_db = db_dependency(self.factory)
        read, write = scope_of(service, "read"), scope_of(service, "write")
        h = self

        @router.get("/thing", dependencies=[Depends(require(service, read))])
        def thing(request: Request) -> JSONResponse:
            h.hits += 1
            data = {"status": "delivered", "name": "星川", "count": 3, "items": [{"n": 1}, {"n": 2}]}
            return request.app.state.sim.style.ok(data, request_id=request.state.request_id)

        @router.get("/things/{thing_id}", dependencies=[Depends(require(service, read))])
        def thing_by_id(thing_id: str, request: Request) -> JSONResponse:
            return request.app.state.sim.style.ok({"id": thing_id}, request_id=request.state.request_id)

        @router.get("/notes/count", dependencies=[Depends(require(service, read))])
        def count(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
            n = db.scalar(select(func.count()).select_from(Note))
            return request.app.state.sim.style.ok({"count": n}, request_id=request.state.request_id)

        @router.post("/notes", dependencies=[Depends(require(service, write))])
        def create_note(
            request: Request, body: dict = Body(...), key: str = Depends(idempotency_key), db: Session = Depends(get_db)
        ) -> JSONResponse:
            def work(s: Session):
                if h.fail_next:
                    h.fail_next = False
                    raise ApiError(422, "VALIDATION", "故意失败")
                note = Note(text=body.get("text", ""))
                s.add(note)
                s.flush()
                return 201, {"id": note.id, "text": note.text}

            return run_idempotent(request, db, key, body, work)

        @router.post("/slow-notes", dependencies=[Depends(require(service, write))])
        def slow_note(
            request: Request, body: dict = Body(...), key: str = Depends(idempotency_key), db: Session = Depends(get_db)
        ) -> JSONResponse:
            def work(s: Session):
                h.slow_started.set()
                h.slow_release.wait(10)
                note = Note(text=body.get("text", ""))
                s.add(note)
                s.flush()
                return 201, {"id": note.id}

            return run_idempotent(request, db, key, body, work)

        self.app = build_app(
            service=service,
            title=f"test-{service}",
            routers=[router],
            session_factory=self.factory,
            tick=lambda now: (self.ticks.append(now), {"processed": 7})[1],
            on_event=self.events.append,
            on_reset=self.resets.append,
        )

    def client(self, kind: str | None = None, **kw) -> TestClient:
        token = get_settings().tokens[kind or kind_for(self.service)]
        c = TestClient(self.app, **kw)
        c.auth = TokenAuth(token)
        return c

    def note_count(self) -> int:
        with self.factory() as s:
            return s.scalar(select(func.count()).select_from(Note))

    def p(self, path: str) -> str:
        return f"/{self.service}/v1{path}"

    def sim(self, path: str) -> str:
        return f"/{self.service}/_sim{path}"


def logical(service: str, resp) -> tuple[int, str | int | None]:
    """把不同风格的错误响应还原成 (HTTP 状态, 错误码)。成功返回 (状态, None)。"""
    body = resp.json()
    if service == "catalog":
        return resp.status_code, body.get("errorCode")
    if service in ("order", "aftersale"):
        return resp.status_code, body["code"] if body.get("code") else None
    if service == "logistics":
        return resp.status_code, body["errCode"] if body.get("success") == "false" else None
    if service == "payment":
        return resp.status_code, body.get("code") if "status" in body and "type" in body else None
    if service == "member":
        return resp.status_code, body["error"]["code"] if body.get("ok") is False else None
    return resp.status_code, body["errors"][0]["code"] if "errors" in body else None


# 手写的期望表（不复用被测代码里的映射）：状态 -> {风格族: (HTTP 状态, 错误码)}
STRING_FAMILY = ("catalog", "payment", "member", "ticket", "control")
EXPECTED = {
    401: {"string": (401, "UNAUTHORIZED"), "number": (401, 40101), "logistics": (200, "B0010")},
    403: {"string": (403, "FORBIDDEN"), "number": (403, 40301), "logistics": (200, "B0011")},
    429: {"string": (429, "RATE_LIMITED"), "number": (429, 42901), "logistics": (200, "B0013")},
    503: {"string": (503, "SERVICE_UNAVAILABLE"), "number": (503, 50301), "logistics": (200, "B0009")},
    504: {"string": (504, "GATEWAY_TIMEOUT"), "number": (504, 50401), "logistics": (200, "B0008")},
}


def expected(service: str, status: int) -> tuple[int, str | int]:
    family = "logistics" if service == "logistics" else "number" if service in ("order", "aftersale") else "string"
    return EXPECTED[status][family]


def internal_headers() -> dict[str, str]:
    return {}
