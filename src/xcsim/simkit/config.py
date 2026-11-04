"""运行配置：全部来自环境变量（见 .env.example），每次调用现读，方便测试改环境。

没有设置任何环境变量也能跑：下面的默认 token 都是虚构的演示值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BJ_TZ = "Asia/Shanghai"
BUSINESS_SERVICES = ("catalog", "order", "logistics", "payment", "member", "aftersale", "ticket")
SERVICES = (*BUSINESS_SERVICES, "control")

DEFAULT_TOKENS = {
    "cs-agent": "xc-demo-cs-agent-token",
    "internal": "xc-demo-internal-token",
    "control": "xc-demo-control-token",
}
_TOKEN_ENV = {
    "cs-agent": "XC_TOKEN_CS_AGENT",
    "internal": "XC_TOKEN_INTERNAL",
    "control": "XC_TOKEN_CONTROL",
}
DEFAULT_ANCHOR = "2026-11-01T10:00:00+08:00"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Settings:
    anchor: datetime
    seed: int
    auto_settle_seconds: float  # <= 0 表示关闭墙钟自动推进
    tokens: dict[str, str]
    base_url: str
    data_dir: Path


def parse_anchor(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(BJ_TZ))


def get_settings() -> Settings:
    env = os.environ
    return Settings(
        anchor=parse_anchor(env.get("XC_ANCHOR", DEFAULT_ANCHOR)),
        seed=int(env.get("XC_SEED", "42")),
        auto_settle_seconds=float(env.get("XC_AUTO_SETTLE_SECONDS", "5")),
        tokens={k: env.get(v, DEFAULT_TOKENS[k]) for k, v in _TOKEN_ENV.items()},
        base_url=env.get("XC_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        data_dir=Path(env.get("XC_DATA_DIR", "data")),
    )


def token_for(kind: str) -> str:
    return get_settings().tokens[kind]


def service_base_url(service: str) -> str:
    """服务基地址。单进程模式只用 XC_BASE_URL；compose 模式用 XC_<SERVICE>_URL 逐个覆盖。"""
    override = os.environ.get(f"XC_{service.upper()}_URL")
    return (override or get_settings().base_url).rstrip("/")


def db_path(service: str) -> Path:
    """每个服务一个库文件。"""
    return get_settings().data_dir / f"{service}.db"


def snapshot_path(service: str, seed: int) -> Path:
    return get_settings().data_dir / "snapshots" / str(seed) / f"{service}.db"
