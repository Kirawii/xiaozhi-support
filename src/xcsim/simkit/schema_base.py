"""各服务 schemas.py 共用的基类与包络类型。

CamelModel：对外 camelCase（catalog/logistics/member）；内部和测试仍可用 snake_case 构造。
SnakeModel：对外 snake_case（order/aftersale/payment/ticket/control）。
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class SnakeModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class CodeEnvelope(SnakeModel, Generic[T]):
    """order / aftersale：{code, message, data, request_id}"""

    code: int = 0
    message: str = "ok"
    data: T
    request_id: str = ""


class LogisticsEnvelope(CamelModel, Generic[T]):
    """logistics：永远 HTTP 200，看 success；success 是字符串 "true"/"false"。"""

    success: str = "true"
    err_code: str = "0000"
    err_msg: str = ""
    data: T | None = None


class OkErrorInfo(CamelModel):
    code: str
    message: str


class OkEnvelope(CamelModel, Generic[T]):
    """member：{ok, result, error}"""

    ok: bool = True
    result: T | None = None
    error: OkErrorInfo | None = None
