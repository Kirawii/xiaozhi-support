"""服务间 HTTP 客户端（同步 httpx）。基地址来自配置，单进程和 compose 模式路径相同。"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx

from .config import get_settings, service_base_url
from .styles import STYLES


class TokenAuth(httpx.Auth):
    """按请求路径的第一段（服务名）选择鉴权头：Bearer / X-Express-Token / X-Control-Token。"""

    def __init__(self, token: str) -> None:
        self.token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        segments = request.url.path.split("/")
        service = segments[1] if len(segments) > 1 else ""
        style = STYLES.get(service)
        header = style.auth_header if style else "Authorization"
        request.headers[header] = f"Bearer {self.token}" if header == "Authorization" else self.token
        yield request


class DependencyError(Exception):
    """下游服务连不上或超时。调用方应转成 50301 DEPENDENCY_UNAVAILABLE。"""

    def __init__(self, service: str, cause: Exception) -> None:
        super().__init__(f"下游服务 {service} 不可用: {cause}")
        self.service = service
        self.cause = cause


class ServiceClient:
    def __init__(
        self,
        service: str,
        *,
        kind: str = "internal",
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        base_url: str | None = None,
    ) -> None:
        self.service = service
        self._client = httpx.Client(
            base_url=base_url or service_base_url(service),
            auth=TokenAuth(get_settings().tokens[kind]),
            timeout=timeout,
            transport=transport,
            trust_env=False,  # 服务间调用走本机/内网，不经系统代理
        )

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:  # 连接失败、超时、读取中断
            raise DependencyError(self.service, exc) from exc

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, *, idempotency_key: str | None = None, **kwargs: Any) -> httpx.Response:
        if idempotency_key:
            kwargs["headers"] = {**kwargs.get("headers", {}), "Idempotency-Key": idempotency_key}
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ServiceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
