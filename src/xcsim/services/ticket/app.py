from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="ticket",
        title="星川商城 · 工单服务 ticket",
        description=(
            "转人工工单、消息、排队。风格：snake_case 裸 JSON，错误为 {errors:[{code,field,message}]}。"
            "内部备注只有 internal token 可见。（当前为接口桩）"
        ),
        routers=[router],
    )
