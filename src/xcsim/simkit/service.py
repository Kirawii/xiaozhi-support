"""build_app：把一个服务需要的 simkit 能力一次挂好。

每个服务的 app.py 只需要调用 build_app(service=..., routers=[...])：
鉴权由各路由自己声明，错误风格、中间件、/health、/v1/time、/_sim/* 在这里统一提供。
路径带服务前缀（/catalog/v1/...），所以单进程和 docker-compose 模式路径完全一致。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import clock, fmt
from .auth import require_internal
from .errors import ApiError
from .events import EventInbox, EventReporter
from .faults import FaultRegistry, FaultRule
from .idempotency import IdempotencyStore
from .middleware import SimMiddleware
from .styles import Style, style_for

TickCallback = Callable[[datetime], dict[str, Any] | None]
EventCallback = Callable[[dict[str, Any]], None]
ResetCallback = Callable[[dict[str, Any]], None]
SyncCallback = Callable[[dict[str, Any]], None]


@dataclass
class SimContext:
    """挂在 app.state.sim 上的、该服务的仿真状态。"""

    service: str
    style: Style
    faults: FaultRegistry
    inbox: EventInbox
    reporter: EventReporter
    session_factory: sessionmaker[Session] | None = None
    idem_store: IdempotencyStore | None = None
    tick_cb: TickCallback | None = None
    reset_cb: ResetCallback | None = None
    sync_cb: SyncCallback | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ /_sim 请求体
class SyncPayload(BaseModel):
    clock: dict[str, Any] | None = None  # {sim_time, mode, rate}
    faults: list[FaultRule] | None = None
    profile: str | None = None
    seed: int | None = None


class TickPayload(BaseModel):
    now: str | None = None


class ResetPayload(BaseModel):
    seed: int | None = None
    profile: str | None = None


class EventsPayload(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def make_sim_router(service: str) -> APIRouter:
    """/{service}/_sim/{sync,tick,reset,events}：只接受 internal token，不进 OpenAPI。"""
    router = APIRouter(
        prefix=f"/{service}/_sim", include_in_schema=False, dependencies=[Depends(require_internal(service))]
    )

    def ctx_of(request: Request) -> SimContext:
        return request.app.state.sim

    @router.post("/sync")
    def sync(payload: SyncPayload, request: Request) -> JSONResponse:
        ctx = ctx_of(request)
        if payload.clock is not None:
            clock.clock.apply(payload.clock)
        if payload.profile is not None:
            try:
                ctx.faults.set_profile(payload.profile)
            except ValueError as exc:
                raise ApiError(422, "VALIDATION", str(exc), field="profile") from exc
        if payload.faults is not None or payload.seed is not None:
            ctx.faults.replace(payload.faults if payload.faults is not None else ctx.faults.rules(), payload.seed)
        if ctx.sync_cb:
            ctx.sync_cb(payload.model_dump())
        data = {"sim": clock.clock.snapshot(), "faults": len(ctx.faults.rules()), "profile": ctx.faults.profile}
        return ctx.style.ok(data, request_id=_rid(request))

    @router.post("/tick")
    def tick(payload: TickPayload, request: Request) -> JSONResponse:
        ctx = ctx_of(request)
        now = fmt.parse_dt(payload.now) if payload.now else clock.now()
        result = ctx.tick_cb(now) if ctx.tick_cb else None
        return ctx.style.ok(result or {"processed": 0}, request_id=_rid(request))

    @router.post("/reset")
    def reset(payload: ResetPayload, request: Request) -> JSONResponse:
        ctx = ctx_of(request)
        if payload.seed is not None:
            ctx.faults.seed = payload.seed
        ctx.faults.reset_state()
        ctx.inbox.clear()
        if payload.profile:
            ctx.faults.set_profile(payload.profile)
        if ctx.reset_cb:
            ctx.reset_cb(payload.model_dump())
        return ctx.style.ok({"reset": True, "service": service}, request_id=_rid(request))

    @router.post("/events")
    def events(payload: EventsPayload, request: Request) -> JSONResponse:
        ctx = ctx_of(request)
        return ctx.style.ok(ctx.inbox.receive(payload.events), request_id=_rid(request))

    return router


def make_infra_router(service: str) -> APIRouter:
    """/{service}/health 和 /{service}/v1/time。"""
    router = APIRouter(tags=["infra"])

    @router.get(f"/{service}/health", summary="健康检查（无需鉴权）")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": service}

    @router.get(
        f"/{service}/v1/time",
        summary="仿真时间（Agent 必须从这里取“今天”，不能用系统时间）",
        response_class=JSONResponse,
    )
    def sim_time(request: Request) -> JSONResponse:
        ctx: SimContext = request.app.state.sim
        return ctx.style.ok(ctx.style.time_data(clock.clock.snapshot()), request_id=_rid(request))

    return router


def _install_error_handlers(app: FastAPI) -> None:
    def style_of(request: Request) -> Style:
        return request.app.state.sim.style

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return style_of(request).error_response(exc, _rid(request))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(x) for x in e["loc"]], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        first = errors[0] if errors else {"loc": [], "msg": "请求参数有误"}
        err = ApiError(
            400, "BAD_REQUEST", f"参数校验失败: {'.'.join(first['loc'])} {first['msg']}",
            field=".".join(first["loc"][1:]) or None, detail={"errors": errors},
        )
        return style_of(request).error_response(err, _rid(request))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        err = ApiError(exc.status_code, None, "接口路径不存在" if exc.status_code == 404 else str(exc.detail))
        return style_of(request).error_response(err, _rid(request))


def build_app(
    *,
    service: str,
    title: str,
    routers: Sequence[APIRouter],
    description: str = "",
    session_factory: sessionmaker[Session] | None = None,
    tick: TickCallback | None = None,
    on_event: EventCallback | None = None,
    on_reset: ResetCallback | None = None,
    on_sync: SyncCallback | None = None,
    seed: int | None = None,
) -> FastAPI:
    from .config import get_settings

    app = FastAPI(
        title=title,
        description=description,
        version="0.1.0",
        openapi_url=f"/{service}/openapi.json",
        docs_url=f"/{service}/docs",
        redoc_url=None,
    )
    inbox = EventInbox()
    inbox.handler = on_event
    app.state.sim = SimContext(
        service=service,
        style=style_for(service),
        faults=FaultRegistry(service, get_settings().seed if seed is None else seed),
        inbox=inbox,
        reporter=EventReporter(service),
        session_factory=session_factory,
        idem_store=IdempotencyStore(session_factory) if session_factory else None,
        tick_cb=tick,
        reset_cb=on_reset,
        sync_cb=on_sync,
    )
    _install_error_handlers(app)
    app.add_middleware(SimMiddleware, service=service)
    app.include_router(make_infra_router(service))
    app.include_router(make_sim_router(service))
    for r in routers:
        app.include_router(r)
    return app
