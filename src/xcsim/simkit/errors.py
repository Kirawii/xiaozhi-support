"""业务异常。路由里 raise ApiError，由各服务自己的“错误风格”渲染成响应。

code 可以写：
- 服务自己的错误码（"SPU_NOT_FOUND"、40401、"B0001" 等，原样输出）；
- simkit 的通用符号（见 styles.SYMBOLS，如 "UNAUTHORIZED"），各风格会翻译成自己的写法；
- None：按 HTTP 状态取通用符号。
"""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str | int | None = None,
        message: str = "",
        *,
        field: str | None = None,
        detail: dict[str, Any] | None = None,
        rule_code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or str(code))
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.detail = detail
        self.rule_code = rule_code
        self.headers = headers or {}
