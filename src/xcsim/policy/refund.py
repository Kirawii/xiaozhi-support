"""退款：人工复核、先行退款门槛（C2）、到账预计时间。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .loader import get_policy
from .models import ArrivalDays, Policy, fen
from .timeutil import add_business_days, to_bj


def needs_manual_review(gross_refund_fen: int, policy: Policy | None = None) -> bool:
    """应退金额超过 2000 元（不含）要人工复核。

    C2：金额是扣取件费等费用之前的现金+余额部分，不含积分。
    """
    return gross_refund_fen > fen((policy or get_policy()).refund.manual_review_over)


def manual_review_due_at(started_at: datetime, policy: Policy | None = None) -> datetime:
    """复核最晚完成时间：1 个工作日（周日提交落到周一）。"""
    p = policy or get_policy()
    return add_business_days(started_at, p.return_7day.manual_review_business_days, p)


@dataclass(frozen=True)
class AdvanceRefundDecision:
    allowed: bool
    reason: str | None  # None / tier / amount / no_tracking / suspended


def advance_refund_decision(
    *,
    tier: str,
    gross_refund_fen: int,
    has_valid_return_tracking: bool,
    suspended_until: datetime | None,
    now: datetime,
    policy: Policy | None = None,
) -> AdvanceRefundDecision:
    """先行退款：金星、<=500 元（含）、已登记有效寄回单号、资格未被暂停。

    tier 取申请时的当前等级（C3）。
    """
    rule = (policy or get_policy()).refund.advance_refund
    if tier != rule.tier:
        return AdvanceRefundDecision(False, "tier")
    if gross_refund_fen > fen(rule.max_amount):
        return AdvanceRefundDecision(False, "amount")
    if not has_valid_return_tracking:
        return AdvanceRefundDecision(False, "no_tracking")
    if suspended_until is not None and to_bj(now) < to_bj(suspended_until):
        return AdvanceRefundDecision(False, "suspended")
    return AdvanceRefundDecision(True, None)


def suspension_until(inspect_failed_at: datetime, policy: Policy | None = None) -> datetime:
    """先行退款后验收不合格：暂停资格 90 天。"""
    p = policy or get_policy()
    return to_bj(inspect_failed_at, p) + timedelta(days=p.refund.advance_refund.suspension_days_on_inspect_fail)


def _arrival_rule(channel: str, policy: Policy) -> ArrivalDays:
    table = policy.refund.channel_arrival_business_days
    key = "mobile_pay" if channel.startswith("mobile_pay") else channel
    if key not in ("balance", "mobile_pay", "debit_card", "credit_card"):
        raise ValueError(f"未知退款渠道: {channel}")
    return getattr(table, key)


def arrival_eta(
    channel: str, refund_started_at: datetime, policy: Policy | None = None
) -> tuple[datetime, datetime]:
    """预计到账时间区间（最早、最晚），按工作日从退款发起时刻起算。"""
    p = policy or get_policy()
    rule = _arrival_rule(channel, p)
    return (
        add_business_days(refund_started_at, rule.min, p),
        add_business_days(refund_started_at, rule.max, p),
    )


def points_refund_amount(value_fen: int, policy: Policy | None = None) -> int:
    """积分抵扣部分退回多少积分（100 积分 = 1 元，用 membership.points.redeem_rate）。"""
    rate = (policy or get_policy()).membership.points.redeem_rate
    return value_fen * rate // 100


def clawback_shortfall_deduction_fen(shortfall_points: int, policy: Policy | None = None) -> int:
    """P03：积分余额不足扣回的部分，按 100 积分 = 1 元从退款中扣除（返回分）。"""
    rate = (policy or get_policy()).refund.points_clawback_rate
    return max(shortfall_points, 0) * 100 // rate


def installment_fee_refundable(policy: Policy | None = None) -> bool:
    return (policy or get_policy()).refund.installment_fee_refundable
