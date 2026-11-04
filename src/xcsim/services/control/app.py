from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import report_router, router


def create_app() -> FastAPI:
    return build_app(
        service="control",
        title="星川商城 · 控制服务 control",
        description=(
            "时钟、故障、事件、场景、重置、清单。鉴权头 X-Control-Token（cs-agent token 无权访问）。"
            "（当前为接口桩）"
        ),
        routers=[router, report_router],
    )
