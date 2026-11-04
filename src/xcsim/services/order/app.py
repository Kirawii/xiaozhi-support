from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="order",
        title="星川商城 · 订单服务 order",
        description="订单、取消、改地址、拒收。风格：snake_case，{code,message,data,request_id} 包络，金额为元的字符串。（当前为接口桩）",
        routers=[router],
    )
