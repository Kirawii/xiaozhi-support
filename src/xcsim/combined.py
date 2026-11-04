"""单进程组合应用：把 8 个服务挂在同一个进程里，路径与 docker-compose 模式完全相同。

每个服务仍是独立的 FastAPI 应用（各自的中间件、错误风格、OpenAPI）；这里只按路径第一段
（/catalog、/order …）把请求分发给对应服务，不改写路径。
"""
from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from starlette.routing import BaseRoute, Match
from starlette.types import Receive, Scope, Send

from xcsim.simkit.config import SERVICES


class ServiceMount(BaseRoute):
    """按前缀把请求交给子应用；与 Starlette 的 Mount 不同，不会裁剪 path。"""

    def __init__(self, prefix: str, app: FastAPI) -> None:
        self.prefix = prefix
        self.app = app

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == self.prefix or path.startswith(self.prefix + "/"):
                return Match.FULL, {}
        return Match.NONE, {}

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    subapps = {
        name: importlib.import_module(f"xcsim.services.{name}.app").create_app() for name in SERVICES
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 子应用不会收到 lifespan 事件，这里代为进入它们的 lifespan（以后建库、起后台任务用）
        async with AsyncExitStack() as stack:
            for sub in subapps.values():
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    root = FastAPI(title="星川商城仿真环境（单进程）", openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)

    @root.get("/", include_in_schema=False)
    def index() -> dict[str, dict[str, str]]:
        return {name: {"docs": f"/{name}/docs", "openapi": f"/{name}/openapi.json", "health": f"/{name}/health"} for name in SERVICES}

    for name, sub in subapps.items():
        root.router.routes.append(ServiceMount(f"/{name}", sub))
    return root
