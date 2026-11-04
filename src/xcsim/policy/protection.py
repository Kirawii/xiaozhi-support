"""价保、保修、发票期限。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .loader import get_policy
from .models import Policy
from .timeutil import add_months, day_k_from_payment, end_of_day, on_or_before_deadline, to_bj


# ---------------------------------------------------------------- 价保
def price_protection_window_days(tier: str, policy: Policy | None = None) -> int:
    """tier 取申请时的当前等级（C3）。"""
    return getattr(((policy or get_policy()).price_protection.window_days), tier)


@dataclass(frozen=True)
class PriceProtectionResult:
    eligible: bool
    reason: str | None  # None / window / price_type / order_kind / no_diff
    day_k: int
    window_days: int
    diff_fen: int


def price_protection(
    *,
    tier: str,
    paid_at: datetime,
    now: datetime,
    paid_unit_fen: int,
    current_price_fen: int,
    current_price_type: str,
    order_kind: str = "normal",
    policy: Policy | None = None,
) -> PriceProtectionResult:
    """价保判断。付款当日为第 1 天；差额 = 实付单价 - 现价（实付含券后）。

    秒杀、拼团、直播专享、清仓价不适用；预售、含赠品订单不适用。
    """
    p = policy or get_policy()
    rule = p.price_protection
    k = day_k_from_payment(paid_at, now)
    window = price_protection_window_days(tier, p)

    def result(ok: bool, reason: str | None, diff: int = 0) -> PriceProtectionResult:
        return PriceProtectionResult(ok, reason, k, window, diff)

    if k > window:
        return result(False, "window")
    if current_price_type in rule.excluded_price_types:
        return result(False, "price_type")
    # 订单模型里的赠品订单叫 gift_included，policy 里写作 gift，两种写法都认
    if order_kind in rule.excluded_order_kinds or order_kind.removesuffix("_included") in rule.excluded_order_kinds:
        return result(False, "order_kind")
    diff = paid_unit_fen - current_price_fen
    if diff <= 0:
        return result(False, "no_diff")
    return result(True, None, diff)


# ---------------------------------------------------------------- 保修
def warranty_months(*, refurbished: bool, spu_months: int | None = None, policy: Policy | None = None) -> int:
    w = (policy or get_policy()).warranty
    if refurbished:
        return w.refurbished_months
    return spu_months if spu_months is not None else w.default_months


def warranty_end(signed_at: datetime, months: int, policy: Policy | None = None) -> datetime:
    """C5：签收日起算（含当日），截止到 N 个月后同日的前一天 23:59:59。"""
    p = policy or get_policy()
    start = to_bj(signed_at, p).date()
    return end_of_day(add_months(start, months) - timedelta(days=1), p)


def in_warranty(signed_at: datetime, now: datetime, months: int, policy: Policy | None = None) -> bool:
    return on_or_before_deadline(now, warranty_end(signed_at, months, policy))


def part_warranty_end(start_at: datetime, days: int, policy: Policy | None = None) -> datetime:
    """零部件保修/付费维修后保修：自起算日（含）连续 days 天。"""
    p = policy or get_policy()
    return end_of_day(to_bj(start_at, p).date() + timedelta(days=days - 1), p)


def paid_repair_warranty_end(repaired_at: datetime, policy: Policy | None = None) -> datetime:
    p = policy or get_policy()
    return part_warranty_end(repaired_at, p.warranty.paid_repair_part_warranty_days, p)


def replacement_due(failed_repairs: int, policy: Policy | None = None) -> bool:
    """同一问题维修失败达到 2 次可换新。"""
    return failed_repairs >= (policy or get_policy()).warranty.replace_after_failed_repairs


# ---------------------------------------------------------------- 发票
def completion_time(delivered_at: datetime, policy: Policy | None = None) -> datetime:
    """订单完成时间 = 签收后 7 天。"""
    p = policy or get_policy()
    return to_bj(delivered_at, p) + timedelta(days=p.invoice.completion_after_delivery_days)


def invoice_deadline(completed_at: datetime, policy: Policy | None = None) -> datetime:
    """开票截止：完成日后第 180 天当日 23:59:59。"""
    p = policy or get_policy()
    d: date = to_bj(completed_at, p).date() + timedelta(days=p.invoice.apply_within_days_after_completion)
    return end_of_day(d, p)


def can_issue_invoice(completed_at: datetime, now: datetime, policy: Policy | None = None) -> bool:
    return on_or_before_deadline(now, invoice_deadline(completed_at, policy))
