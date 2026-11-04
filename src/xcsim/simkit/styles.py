"""7 种刻意不统一的响应风格（§3.2）。

每种风格是一个“响应工厂”：
- ``wrap(data)``：把成功数据包进该服务的包络；
- ``ok(data, status)``：生成成功响应；
- ``error_response(err)``：把 ApiError 渲染成该服务的错误形态（同一风格能表达
  400/401/403/404/409/422/429/503/504）；
- ``time_data(state)``：该服务格式的“仿真时间”。

control 服务不在 §3.2 的表里，沿用 ticket 的风格（裸 JSON + errors 列表）。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi.responses import JSONResponse

from . import fmt
from .errors import ApiError

# 通用符号 -> (HTTP 状态, 字符串码, 数字码, 物流码)
SYMBOLS: dict[str, tuple[int, str, int, str]] = {
    "BAD_REQUEST": (400, "BAD_REQUEST", 40001, "B0006"),
    "IDEM_MISSING": (400, "IDEMPOTENCY_KEY_REQUIRED", 40002, "B0007"),
    "UNAUTHORIZED": (401, "UNAUTHORIZED", 40101, "B0010"),
    "FORBIDDEN": (403, "FORBIDDEN", 40301, "B0011"),
    "NOT_FOUND": (404, "NOT_FOUND", 40401, "B0001"),
    "METHOD_NOT_ALLOWED": (405, "METHOD_NOT_ALLOWED", 40501, "B0006"),
    "CONFLICT": (409, "CONFLICT", 40901, "B0004"),
    "IDEM_IN_PROGRESS": (409, "IDEMPOTENCY_IN_PROGRESS", 40909, "B0014"),
    "VALIDATION": (422, "VALIDATION_FAILED", 42200, "B0006"),
    "IDEM_REUSED": (422, "IDEMPOTENCY_KEY_REUSED", 42202, "B0015"),
    "RATE_LIMITED": (429, "RATE_LIMITED", 42901, "B0013"),
    "INTERNAL": (500, "INTERNAL_ERROR", 50001, "B0099"),
    "UNAVAILABLE": (503, "SERVICE_UNAVAILABLE", 50301, "B0009"),
    "TIMEOUT": (504, "GATEWAY_TIMEOUT", 50401, "B0008"),
}
_STATUS_SYMBOL = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT", 422: "VALIDATION", 429: "RATE_LIMITED", 500: "INTERNAL", 503: "UNAVAILABLE", 504: "TIMEOUT",
}
_PHRASE = {
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    409: "Conflict", 422: "Unprocessable Entity", 429: "Too Many Requests", 500: "Internal Server Error",
    503: "Service Unavailable", 504: "Gateway Timeout",
}
_DEFAULT_MESSAGE = {
    400: "请求参数有误", 401: "缺少或无效的访问凭证", 403: "凭证没有访问该接口的权限", 404: "资源不存在",
    405: "请求方法不被支持", 409: "请求与当前状态冲突", 422: "请求内容无法处理", 429: "请求过于频繁，请稍后再试",
    500: "服务内部错误", 503: "服务暂时不可用", 504: "网关超时",
}


class Style:
    name = "base"
    auth_header = "Authorization"  # Authorization 表示 Bearer，其余为裸 token 头
    always_ok = False  # True：错误也返回 HTTP 200（物流）
    media_type = "application/json"

    # ---- 子类实现
    def error_body(self, err: ApiError, status: int, code: str | int, message: str, request_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def wrap(self, data: Any, request_id: str | None = None) -> Any:
        return data

    def time_data(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    # ---- 通用
    def code_for(self, err: ApiError) -> str | int:
        raise NotImplementedError

    def http_status(self, status: int) -> int:
        return status

    def error_response(self, err: ApiError, request_id: str = "") -> JSONResponse:
        status = err.status
        message = err.message or _DEFAULT_MESSAGE.get(status, "error")
        body = self.error_body(err, status, self.code_for(err), message, request_id)
        headers = dict(err.headers)
        return JSONResponse(body, status_code=self.http_status(status), headers=headers, media_type=self.media_type)

    def ok(
        self,
        data: Any,
        status: int = 200,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        return JSONResponse(self.wrap(data, request_id), status_code=status, headers=headers)

    def _symbol(self, err: ApiError) -> str | None:
        if err.code is None:
            return _STATUS_SYMBOL.get(err.status, "INTERNAL" if err.status >= 500 else "BAD_REQUEST")
        if isinstance(err.code, str) and err.code in SYMBOLS:
            return err.code
        return None


class _StringCodeStyle(Style):
    def code_for(self, err: ApiError) -> str | int:
        sym = self._symbol(err)
        return SYMBOLS[sym][1] if sym else err.code  # type: ignore[return-value]


class CatalogStyle(_StringCodeStyle):
    """camelCase 裸 JSON；HTTP 状态 + {errorCode, message}。"""

    name = "catalog"

    def error_body(self, err, status, code, message, request_id):
        return {"errorCode": code, "message": message}

    def time_data(self, state):
        return {"now": fmt.iso_bj(datetime.fromisoformat(state["sim_time"])), "mode": state["mode"], "rate": state["rate"]}


class EnvelopeStyle(Style):
    """order/aftersale：{code, message, data, request_id}；HTTP 状态 + 数字 code。"""

    name = "order"

    def code_for(self, err):
        sym = self._symbol(err)
        return SYMBOLS[sym][2] if sym else err.code

    def wrap(self, data, request_id=None):
        return {"code": 0, "message": "ok", "data": data, "request_id": request_id or ""}

    def error_body(self, err, status, code, message, request_id):
        body: dict[str, Any] = {"code": code, "message": message, "data": None, "request_id": request_id}
        if err.rule_code:
            body["rule_code"] = err.rule_code
        if err.detail is not None:
            body["detail"] = err.detail
        return body

    def time_data(self, state):
        return {"now": fmt.iso_bj(datetime.fromisoformat(state["sim_time"])), "mode": state["mode"], "rate": state["rate"]}


class AftersaleStyle(EnvelopeStyle):
    name = "aftersale"


class LogisticsStyle(Style):
    """camelCase 缩写；永远 HTTP 200，看 success / errCode。"""

    name = "logistics"
    auth_header = "X-Express-Token"
    always_ok = True

    def code_for(self, err):
        sym = self._symbol(err)
        return SYMBOLS[sym][3] if sym else err.code

    def http_status(self, status):
        return 200

    def wrap(self, data, request_id=None):
        return {"success": "true", "errCode": "0000", "errMsg": "", "data": data}

    def error_body(self, err, status, code, message, request_id):
        return {"success": "false", "errCode": code, "errMsg": message, "data": None}

    def time_data(self, state):
        return {"serverTime": fmt.bj_str(datetime.fromisoformat(state["sim_time"])), "mode": state["mode"], "rate": state["rate"]}


class PaymentStyle(_StringCodeStyle):
    """snake_case 裸 JSON；错误是 RFC 9457 problem+json；金额整数分，时间 epoch 毫秒。"""

    name = "payment"
    media_type = "application/problem+json"

    def error_body(self, err, status, code, message, request_id):
        slug = re.sub(r"[^a-z0-9]+", "-", str(code).lower()).strip("-")
        body: dict[str, Any] = {
            "type": f"https://errors.xingchuan.invalid/payment/{slug}",
            "title": _PHRASE.get(status, "Error"),
            "status": status,
            "detail": message,
            "code": code,
            "instance": f"/payment/requests/{request_id}",
        }
        if err.detail is not None:
            body["errors"] = err.detail
        return body

    def time_data(self, state):
        return {"now_ms": fmt.epoch_ms(datetime.fromisoformat(state["sim_time"])), "mode": state["mode"], "rate": state["rate"]}


class MemberStyle(_StringCodeStyle):
    """camelCase；{ok, result, error}；404 返回 HTTP 200（看 ok），其余用真实状态码。"""

    name = "member"
    SOFT_STATUSES = frozenset({404})

    def http_status(self, status):
        return 200 if status in self.SOFT_STATUSES else status

    def wrap(self, data, request_id=None):
        return {"ok": True, "result": data, "error": None}

    def error_body(self, err, status, code, message, request_id):
        return {"ok": False, "result": None, "error": {"code": code, "message": message}}

    def time_data(self, state):
        return {"now": fmt.utc_z(datetime.fromisoformat(state["sim_time"])), "mode": state["mode"], "rate": state["rate"]}


class TicketStyle(_StringCodeStyle):
    """snake_case 裸 JSON；HTTP 状态 + {errors: [{code, field, message}]}。"""

    name = "ticket"

    def error_body(self, err, status, code, message, request_id):
        return {"errors": [{"code": code, "field": err.field, "message": message}]}

    def time_data(self, state):
        return {"now": fmt.iso_bj(datetime.fromisoformat(state["sim_time"])), "mode": state["mode"], "rate": state["rate"]}


class ControlStyle(TicketStyle):
    name = "control"
    auth_header = "X-Control-Token"


STYLES: dict[str, Style] = {
    "catalog": CatalogStyle(),
    "order": EnvelopeStyle(),
    "aftersale": AftersaleStyle(),
    "logistics": LogisticsStyle(),
    "payment": PaymentStyle(),
    "member": MemberStyle(),
    "ticket": TicketStyle(),
    "control": ControlStyle(),
}


def style_for(service: str) -> Style:
    return STYLES[service]
