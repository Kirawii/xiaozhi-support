"""所有服务共用的工具：时钟、格式、鉴权、错误风格、幂等、故障、事件、/_sim 路由、客户端、数据库。

这是除 xcsim.policy 之外唯一允许被各服务共同 import 的包。
"""
from . import auth, clock, fmt  # noqa: F401
from .errors import ApiError
from .service import SimContext, build_app

__all__ = ["ApiError", "SimContext", "build_app", "clock", "fmt", "auth"]
