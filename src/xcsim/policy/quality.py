"""质量问题分档、错发漏发、生鲜时限与坏果率退款、检测报告。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .loader import get_policy
from .models import Policy, QualityTier, fen
from .money import mul_ratio
from .timeutil import day_k_after, within_hours


def quality_tier(k: int, policy: Policy | None = None) -> QualityTier:
    """签收后第 k 天所在的质量问题档位（k<1 按第 1 天算）。"""
    p = policy or get_policy()
    k = max(k, 1)
    for tier in p.quality.tiers:
        if k >= tier.from_day and (tier.to_day is None or k <= tier.to_day):
            return tier
    raise ValueError(f"第 {k} 天没有匹配的质量档位")  # pragma: no cover


def quality_options(
    signed_at: datetime,
    now: datetime,
    *,
    exchange_out_of_stock: bool = False,
    policy: Policy | None = None,
) -> tuple[str, ...]:
    """可选的处理方式。第二档里换货无货时可改退款。"""
    tier = quality_tier(day_k_after(signed_at, now), policy)
    options = tuple(tier.options)
    if tier.refund_if_exchange_out_of_stock and exchange_out_of_stock and "refund" not in options:
        options = ("refund", *options)
    return options


def within_wrong_or_missing(signed_at: datetime, now: datetime, policy: Policy | None = None) -> bool:
    """错发漏发：自签收时刻起 48 小时内（含第 48 小时整）。"""
    p = policy or get_policy()
    return within_hours(signed_at, now, p.quality.wrong_or_missing_hours)


def within_fresh_claim(signed_at: datetime, now: datetime, policy: Policy | None = None) -> bool:
    """生鲜反馈：自签收时刻起 24 小时内（含）。"""
    p = policy or get_policy()
    return within_hours(signed_at, now, p.special.fresh.claim_within_hours)


@dataclass(frozen=True)
class FreshRefund:
    refund_fen: int
    full_refund: bool
    cold_chain_refunded: bool


def fresh_refund(
    goods_paid_fen: int,
    cold_chain_fee_fen: int,
    bad_ratio: Decimal | str | int,
    policy: Policy | None = None,
) -> FreshRefund:
    """生鲜坏果率退款。

    坏果率 <= 30%（含）：按比例退商品款，冷链费不退；
    超过 30%：全额退，冷链费一并退。
    """
    p = policy or get_policy()
    ratio = Decimal(str(bad_ratio))
    if ratio <= p.special.fresh.partial_refund_up_to_ratio:
        return FreshRefund(mul_ratio(goods_paid_fen, ratio), False, False)
    return FreshRefund(goods_paid_fen + cold_chain_fee_fen, True, True)


def needs_inspection_report(
    *,
    kind: str,
    unit_price_fen: int,
    is_digital_category: bool,
    policy: Policy | None = None,
) -> bool:
    """是否需要授权网点检测报告：数码单价超过 3000（不含）或指定家电类。"""
    rule = (policy or get_policy()).quality.inspection_report
    if kind in rule.appliance_kinds:
        return True
    return is_digital_category and unit_price_fen > fen(rule.digital_unit_price_over)
