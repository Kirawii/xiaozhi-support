"""接口桩用的小工具。桩返回符合 schema 的示例数据，并带 ``X-Stub: true``，后续工作包会替换成真实实现。"""
from __future__ import annotations

from fastapi import Request, Response


def mark_stub(response: Response) -> None:
    """路由依赖：给响应加 X-Stub 头，明确标记“这是桩”。"""
    response.headers["X-Stub"] = "true"


def rid(request: Request) -> str:
    """当前请求的 X-Request-Id（包络里的 request_id 用）。"""
    return getattr(request.state, "request_id", "")
