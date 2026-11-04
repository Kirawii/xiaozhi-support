"""对外格式工具：各服务刻意使用不同的时间和金额表示（§3.2）。

库内：金额整数分，时间 UTC 毫秒。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .config import BJ_TZ

BJ = ZoneInfo(BJ_TZ)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=BJ)


# ------------------------------------------------------------ 时间
def iso_bj(dt: datetime) -> str:
    """2026-11-01T10:00:00+08:00（catalog/order/aftersale/ticket）"""
    return _aware(dt).astimezone(BJ).isoformat(timespec="seconds")


def utc_z(dt: datetime) -> str:
    """2026-11-01T02:00:00Z（member）"""
    return _aware(dt).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_ms(dt: datetime) -> int:
    """UTC 毫秒（payment，也是库内格式）。整数运算，不经浮点。"""
    delta = _aware(dt).astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).astimezone(BJ).replace(
        microsecond=(ms % 1000) * 1000
    )


def bj_str(dt: datetime) -> str:
    """2026-10-25 14:03:22（logistics，北京时间，无时区标记）"""
    return _aware(dt).astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(text: str) -> datetime:
    """解析 ISO（带或不带时区）、Z 结尾、或 "YYYY-MM-DD HH:MM:SS"；无时区按北京时间。"""
    value = text.strip().replace("Z", "+00:00")
    return _aware(datetime.fromisoformat(value)).astimezone(BJ)


# ------------------------------------------------------------ 金额
def yuan_str(fen: int) -> str:
    """9900 -> "99.00"（catalog/order/aftersale）"""
    sign = "-" if fen < 0 else ""
    q, r = divmod(abs(fen), 100)
    return f"{sign}{q}.{r:02d}"


def yuan_float(fen: int) -> float:
    """9900 -> 99.0（logistics/member，只在输出时转浮点，不参与计算）"""
    return float(Decimal(fen) / 100)


def fen_int(fen: int) -> int:
    """payment 直接用整数分，这里只是让调用处语义清楚。"""
    return int(fen)


def parse_yuan(text: str | int | float | Decimal) -> int:
    """"99.00" / 99 / 99.5 -> 整数分；超过两位小数报错。"""
    value = Decimal(str(text)) * 100
    if value != value.to_integral_value():
        raise ValueError(f"金额最多两位小数: {text}")
    return int(value)
