"""请求中间件：X-Request-Id / X-Sim-Now、故障注入、realistic 配置档（延迟、503、限流）。

用纯 ASGI 写（不用 BaseHTTPMiddleware），这样才能做到：
- timeout_after_commit：先让业务处理并提交，再把响应扣下来挂起，最后返回 504；
- corrupt：拿到完整响应体后再改坏。
故障只作用于业务路径 ``/{service}/v1/*``（不含 /time），不影响 /health、/docs、/_sim。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import auth, clock, fmt
from .errors import ApiError
from .faults import corrupt_body

Captured = tuple[int, list[tuple[bytes, bytes]], bytes]


class SimMiddleware:
    def __init__(self, app: ASGIApp, service: str) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sim = scope["app"].state.sim
        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
        scope.setdefault("state", {})["request_id"] = request_id
        path, method = scope["path"], scope["method"]
        faultable = path.startswith(f"/{self.service}/v1/") and not path.endswith("/v1/time")

        body = b""
        body_json: Any = None
        if faultable:
            body = await _read_body(receive)
            if body and "json" in headers.get("content-type", ""):
                try:
                    body_json = json.loads(body)
                except ValueError:
                    body_json = None
            receive = _replay(body, receive)

        def error(status: int, code: str | None, **kw: Any) -> Captured:
            resp = sim.style.error_response(ApiError(status, code, **kw), request_id)
            return resp.status_code, list(resp.raw_headers), resp.body

        result: Captured
        try:
            decision = None
            if faultable:
                caller = auth.caller_kind(self.service, headers)
                decision = sim.faults.evaluate(method, path, caller, body_json)
                if decision.delay_s > 0:
                    await asyncio.sleep(decision.delay_s)
            kind = decision.kind if decision else None
            if kind == "http_429":
                result = error(429, "RATE_LIMITED", headers={"Retry-After": str(decision.retry_after_s)})
            elif kind == "http_503":
                result = error(503, "UNAVAILABLE", message="服务暂时不可用（故障注入）")
            elif kind == "timeout":
                await asyncio.sleep(decision.rule.hold_ms / 1000 if decision.rule else 0)
                result = error(504, "TIMEOUT", message="网关超时（故障注入）")
            elif kind == "timeout_after_commit":
                await _capture(self.app, scope, receive)  # 业务已执行并提交，响应丢弃
                await asyncio.sleep(decision.rule.hold_ms / 1000 if decision.rule else 0)
                result = error(504, "TIMEOUT", message="网关超时（故障注入，业务可能已执行）")
            elif kind == "corrupt":
                status, raw_headers, payload = await _capture(self.app, scope, receive)
                if status < 400:
                    payload = corrupt_body(payload, decision.rule.mode, decision.rule.field)
                result = status, raw_headers, payload
            else:
                result = await _capture(self.app, scope, receive)
        except Exception:  # 业务代码未处理的异常：按本服务风格返回 500
            result = error(500, "INTERNAL", message="服务内部错误")

        await _send(send, result, request_id)


async def _read_body(receive: Receive) -> bytes:
    chunks = []
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()  # 之后是 http.disconnect

    return replay


async def _capture(app: ASGIApp, scope: Scope, receive: Receive) -> Captured:
    status = 500
    raw_headers: list[tuple[bytes, bytes]] = []
    chunks: list[bytes] = []

    async def capture_send(message: Message) -> None:
        nonlocal status, raw_headers
        if message["type"] == "http.response.start":
            status = message["status"]
            raw_headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, capture_send)
    return status, raw_headers, b"".join(chunks)


async def _send(send: Send, result: Captured, request_id: str) -> None:
    status, raw_headers, body = result
    drop = {b"content-length", b"x-request-id", b"x-sim-now"}
    out = [(k, v) for k, v in raw_headers if k.lower() not in drop]
    out += [
        (b"content-length", str(len(body)).encode()),
        (b"x-request-id", request_id.encode()),
        (b"x-sim-now", fmt.iso_bj(clock.now()).encode()),
    ]
    await send({"type": "http.response.start", "status": status, "headers": out})
    await send({"type": "http.response.body", "body": body})
