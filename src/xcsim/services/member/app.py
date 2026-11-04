from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="member",
        title="星川商城 · 会员服务 member",
        description=(
            "用户、地址、等级、积分、优惠券。风格：camelCase，{ok,result,error} 包络，HTTP 200 与 4xx 混用（看 ok），"
            "金额为浮点数，时间为 UTC。（当前为接口桩）"
        ),
        routers=[router],
    )
