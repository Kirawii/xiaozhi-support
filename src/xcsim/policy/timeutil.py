"""时间工具：北京时区、业务签收时间、第 k 天、工作日。

约定：所有函数接受带时区或不带时区的 datetime；不带时区的一律当作北京时间。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .loader import get_policy
from .models import Policy


def tz(policy: Policy | None = None) -> ZoneInfo:
    return ZoneInfo((policy or get_policy()).meta.timezone)


def to_bj(dt: datetime, policy: Policy | None = None) -> datetime:
    """转成北京时间；naive 视为已是北京时间。"""
    zone = tz(policy)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def bj(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """方便造北京时间。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=tz())


def parse_hms(text: str) -> time:
    """"23:59:59" 或 "16:00" -> time。"""
    parts = [int(x) for x in text.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


def add_months(d: date, months: int) -> date:
    """日期加整月；目标月没有这一天时取月末（如 1-31 加 1 月 = 2-28/29）。"""
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def end_of_day(d: date, policy: Policy | None = None) -> datetime:
    """当日截止时刻（policy 的 deadline_time，默认 23:59:59，北京时间）。"""
    p = policy or get_policy()
    return datetime.combine(d, parse_hms(p.return_7day.deadline_time), tzinfo=tz(p))


def on_or_before_deadline(now: datetime, deadline: datetime) -> bool:
    """按“日”判断是否仍在截止日内（截止日当天整天有效）。"""
    return to_bj(now).date() <= to_bj(deadline).date()


# ---------------------------------------------------------------- 签收与第 k 天
def business_signed_at(
    delivered_at: datetime,
    delivery_mode: str = "hand",
    locker_in_at: datetime | None = None,
) -> datetime:
    """业务签收时间（C4）。快递柜、驿站以入柜/入站时间为准，其余用物流签收时间。"""
    if delivery_mode in ("locker", "station") and locker_in_at is not None:
        return to_bj(locker_in_at)
    return to_bj(delivered_at)


def day_k_after(signed_at: datetime, now: datetime) -> int:
    """签收后第 k 天：签收次日为第 1 天；签收当日返回 0。"""
    diff = (to_bj(now).date() - to_bj(signed_at).date()).days
    return max(diff, 0)


def day_k_from_payment(paid_at: datetime, now: datetime) -> int:
    """价保用：付款当日为第 1 天。"""
    return (to_bj(now).date() - to_bj(paid_at).date()).days + 1


def within_hours(start: datetime, now: datetime, hours: int) -> bool:
    """自 start 起 hours 小时内（含整点）。按真实经过的秒数算，不按日历。"""
    return timedelta(0) <= to_bj(now) - to_bj(start) <= timedelta(hours=hours)


# ---------------------------------------------------------------- 工作日
def is_business_day(d: date, policy: Policy | None = None) -> bool:
    """C6：周一至周五，除 holidays，不模拟调休（adjusted_workdays 为空）。"""
    p = policy or get_policy()
    cal = p.business_calendar
    if d in cal.adjusted_workdays:
        return True
    if d.isoweekday() in cal.weekend_days:
        return False
    return not any(h.from_ <= d <= h.to for h in cal.holidays)


def add_business_days(dt: datetime, n: int, policy: Policy | None = None) -> datetime:
    """加（或减）n 个工作日，保留时分秒。n=0 原样返回。

    从周末发起的“1 个工作日”落在下一个工作日（周日 +1 -> 周一）。
    """
    p = policy or get_policy()
    current = to_bj(dt, p)
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    d = current.date()
    while remaining:
        d += timedelta(days=step)
        if is_business_day(d, p):
            remaining -= 1
    return datetime.combine(d, current.timetz())


def in_service_hours(now: datetime, policy: Policy | None = None) -> bool:
    """人工客服在线时段 [open, close)。"""
    p = policy or get_policy()
    t = to_bj(now, p).time()
    hours = p.service_hours.human
    return parse_hms(hours.open) <= t < parse_hms(hours.close)
