from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="aftersale",
        title="星川商城 · 售后服务 aftersale",
        description=(
            "售后申请（强校验规则，无预检接口）、价保、发票。风格同 order："
            "snake_case，{code,message,data,request_id} 包络，金额为元的字符串。（当前为接口桩）"
        ),
        routers=[router],
    )
