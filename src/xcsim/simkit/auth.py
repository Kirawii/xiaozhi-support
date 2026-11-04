"""鉴权：token -> 身份 -> scope（§3.2）。

- 缺失或无效 token：401；有效但 scope 不够：403。
- 多数服务用 ``Authorization: Bearer``；logistics 用 ``X-Express-Token``；control 用 ``X-Control-Token``。
- 三类 token：cs-agent（学习者，只能读写业务服务）、internal（服务间，另可看内部备注、调 /_sim）、
  control（只能用控制接口）。
"""
from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPBearer

from .config import BUSINESS_SERVICES, get_settings
from .errors import ApiError
from .styles import style_for


@dataclass(frozen=True)
class Principal:
    kind: str
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def _business_scopes() -> set[str]:
    return {f"{s}:{a}" for s in BUSINESS_SERVICES for a in ("read", "write")}


def scopes_for(kind: str) -> frozenset[str]:
    if kind == "cs-agent":
        return frozenset(_business_scopes())
    if kind == "internal":
        return frozenset(_business_scopes() | {"ticket:internal_notes", "sim:internal", "control:report"})
    if kind == "control":
        return frozenset({"control:admin"})
    raise KeyError(kind)


def identify(token: str | None) -> Principal | None:
    """按配置的 token 找身份；常数时间比较。"""
    if not token:
        return None
    found: str | None = None
    for kind, value in get_settings().tokens.items():
        if hmac.compare_digest(token.encode(), value.encode()):
            found = kind
    return Principal(found, scopes_for(found)) if found else None


def extract_token(service: str, headers: Mapping[str, str]) -> str | None:
    header = style_for(service).auth_header
    raw = headers.get(header)
    if raw is None:
        return None
    if header == "Authorization":
        scheme, _, value = raw.partition(" ")
        return value.strip() if scheme.lower() == "bearer" and value.strip() else None
    return raw.strip() or None


def caller_kind(service: str, headers: Mapping[str, str]) -> str | None:
    """给故障/限流用：只识别身份，不做 scope 校验。"""
    p = identify(extract_token(service, headers))
    return p.kind if p else None


@lru_cache(maxsize=None)
def _scheme(service: str):
    """只为了让 /docs 页面出现“Authorize”按钮；真正校验在 require()。"""
    header = style_for(service).auth_header
    if header == "Authorization":
        return HTTPBearer(auto_error=False)
    return APIKeyHeader(name=header, auto_error=False)


@lru_cache(maxsize=None)
def require(service: str, scope: str | None = None) -> Callable[..., Principal]:
    """生成一个依赖：校验 token，并要求具备 scope。"""

    def dependency(request: Request, _doc=Depends(_scheme(service))) -> Principal:
        principal = identify(extract_token(service, request.headers))
        if principal is None:
            raise ApiError(401, "UNAUTHORIZED", "缺少或无效的访问凭证")
        if scope is not None and not principal.has(scope):
            raise ApiError(403, "FORBIDDEN", f"凭证 {principal.kind} 没有权限: {scope}")
        request.state.principal = principal
        return principal

    return dependency


def require_internal(service: str) -> Callable[..., Principal]:
    """/_sim/* 只接受 internal token。"""
    return require(service, "sim:internal")
