from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="catalog",
        title="星川商城 · 商品服务 catalog",
        description="商品、SKU、价格历史、库存。风格：camelCase、裸 JSON、金额为元的字符串。（当前为接口桩）",
        routers=[router],
    )
