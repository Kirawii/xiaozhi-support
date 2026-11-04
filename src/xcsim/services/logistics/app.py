from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="logistics",
        title="星川商城 · 物流服务 logistics",
        description=(
            "轨迹、包裹、签收信息、报障。风格：camelCase 缩写，永远 HTTP 200，看 success/errCode；"
            "鉴权头 X-Express-Token；时间为北京时间字符串。（当前为接口桩）"
        ),
        routers=[router],
    )
