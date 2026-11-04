from __future__ import annotations

from fastapi import FastAPI

from xcsim.simkit import build_app

from .routes import router


def create_app() -> FastAPI:
    return build_app(
        service="payment",
        title="星川商城 · 支付与退款服务 payment",
        description=(
            "支付记录、退款（异步、幂等、可能超时未知）、余额。风格：snake_case 裸 JSON，错误为 problem+json，"
            "金额为整数分，时间为 epoch 毫秒。（当前为接口桩）"
        ),
        routers=[router],
    )
