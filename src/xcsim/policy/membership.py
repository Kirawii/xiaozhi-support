"""会员等级评定与积分。

积分约定（policy 只给了倍率，这里把“1 倍 = 每实付 1 元得 1 分”写死在函数里并在测试中固定）：
获得 = floor(实付元 × 倍率)；100 积分 = 1 元即 1 积分 = 1 分钱。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .loader import get_policy
from .models import Policy, fen
from .money import div_round, floor_ratio
from .timeutil import add_months, to_bj, tz


# ---------------------------------------------------------------- 等级
def tier_for_spend(net_spend_fen: int, policy: Policy | None = None) -> str:
    """净消费（实付 - 已退款，含门槛）评定等级。"""
    t = (policy or get_policy()).membership.tiers
    if net_spend_fen >= fen(t.gold):
        return "gold"
    if net_spend_fen >= fen(t.silver):
        return "silver"
    return "normal"


def tier_window(assess_at: datetime, policy: Policy | None = None) -> tuple[datetime, datetime]:
    """评定窗口 [from, to)：to = 评定当月 1 日 00:00，from = to 往前 12 个月。"""
    p = policy or get_policy()
    a = to_bj(assess_at, p)
    to_d = date(a.year, a.month, 1)
    from_d = add_months(to_d, -p.membership.lookback_months)
    zone = tz(p)
    return (
        datetime.combine(from_d, datetime.min.time(), tzinfo=zone),
        datetime.combine(to_d, datetime.min.time(), tzinfo=zone),
    )


def next_assessment_at(now: datetime, policy: Policy | None = None) -> datetime:
    """下一次评定时刻：每月 reassess_day 日 00:00，严格晚于 now。"""
    p = policy or get_policy()
    n = to_bj(now, p)
    day = p.membership.reassess_day_of_month
    candidate = datetime.combine(date(n.year, n.month, day), datetime.min.time(), tzinfo=tz(p))
    if candidate <= n:
        nxt = add_months(date(n.year, n.month, 1), 1)
        candidate = datetime.combine(date(nxt.year, nxt.month, day), datetime.min.time(), tzinfo=tz(p))
    return candidate


# ---------------------------------------------------------------- 积分
def points_multiplier(tier: str, birthday: bool = False, policy: Policy | None = None) -> Decimal:
    pts = (policy or get_policy()).membership.points
    if birthday and tier in ("silver", "gold"):
        return getattr(pts.birthday_multiplier, tier)
    return getattr(pts.multiplier, tier)


def is_birthday(birthday: date, today: date) -> bool:
    return (birthday.month, birthday.day) == (today.month, today.day)


def earn_points(paid_fen: int, tier: str, birthday: bool = False, policy: Policy | None = None) -> int:
    """实付金额（分）换算积分，向下取整。tier 取下单时快照（C3）。"""
    mult = points_multiplier(tier, birthday, policy)
    return int((Decimal(paid_fen) * mult / 100).to_integral_value(rounding="ROUND_FLOOR"))


def points_available_at(signed_at: datetime, policy: Policy | None = None) -> datetime:
    """积分在签收后 7 天（精确到时刻）到账。"""
    p = policy or get_policy()
    return to_bj(signed_at, p) + timedelta(days=p.membership.points.credit_after_delivery_days)


def points_expiry(earned_at: datetime, policy: Policy | None = None) -> datetime:
    """到期：获得年份的下一年 12-31 23:59:59。"""
    p = policy or get_policy()
    year = to_bj(earned_at, p).year + 1
    return datetime(year, 12, 31, 23, 59, 59, tzinfo=tz(p))


def points_to_fen(points: int, policy: Policy | None = None) -> int:
    rate = (policy or get_policy()).membership.points.redeem_rate
    return points * 100 // rate


def max_redeemable_points(payable_fen: int, policy: Policy | None = None) -> int:
    """单笔最多抵扣：应付金额的 10%（向下取整到分），再换算成积分。"""
    pts = (policy or get_policy()).membership.points
    cap_fen = floor_ratio(payable_fen, pts.redeem_cap_ratio)
    return cap_fen * pts.redeem_rate // 100


def clawback_points(earned: int, paid_fen: int, refunded_fen: int) -> int:
    """退款后扣回已获得积分：按退款占实付的比例，四舍五入，不超过已获得。"""
    if paid_fen <= 0 or earned <= 0:
        return 0
    return min(earned, div_round(earned * min(refunded_fen, paid_fen), paid_fen))

