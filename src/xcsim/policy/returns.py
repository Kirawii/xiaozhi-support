"""七天无理由：窗口、次数、不可退品类、换货、取件费与运费险。"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .loader import get_policy
from .models import Policy, fen
from .timeutil import end_of_day, on_or_before_deadline, to_bj


@dataclass(frozen=True)
class ReturnWindow:
    version: Literal["legacy", "current"]
    window_days: int
    deadline_at: datetime
    policy_key: str
    policy_version: str


def is_legacy_by_signed(signed_at: datetime, policy: Policy | None = None) -> bool:
    """签收时间早于 cutover 当天 00:00 即旧版（F1：退货看签收，不看付款）。"""
    p = policy or get_policy()
    return to_bj(signed_at, p).date() < p.meta.cutover_date


def return_window(signed_at: datetime, policy: Policy | None = None) -> ReturnWindow:
    """无理由退货窗口。签收次日为第 1 天，截止日 23:59:59。"""
    p = policy or get_policy()
    if is_legacy_by_signed(signed_at, p):
        version, days, key = "legacy", p.legacy_return.window_days, "legacy_return.window_days"
    else:
        version, days, key = "current", p.return_7day.window_days, "return_7day.window_days"
    deadline = end_of_day(to_bj(signed_at, p).date() + timedelta(days=days), p)
    return ReturnWindow(version, days, deadline, key, p.meta.version)  # type: ignore[arg-type]


def is_return_window_open(window: ReturnWindow, now: datetime) -> bool:
    return on_or_before_deadline(now, window.deadline_at)


# ---------------------------------------------------------------- 申请次数
def can_apply_again(prior_applications: int, policy: Policy | None = None) -> bool:
    """每个订单行最多 2 次，已撤销的也计入。"""
    p = policy or get_policy()
    return prior_applications < p.return_7day.max_applications_per_order_item


def remaining_applications(prior_applications: int, policy: Policy | None = None) -> int:
    p = policy or get_policy()
    return max(p.return_7day.max_applications_per_order_item - prior_applications, 0)


# ---------------------------------------------------------------- 不可退品类
_CONDITION_RULE = {
    "opened": "AS_OPENED",
    "activated": "AS_ACTIVATED",
    "seal_removed": "AS_SEAL_REMOVED",
}


def return_block_rule(
    *,
    tags: Collection[str],
    kind: str = "",
    opened: bool = False,
    activated: bool = False,
    seal_removed: bool = False,
    policy: Policy | None = None,
) -> str | None:
    """商品属于不支持无理由的品类时返回拒绝的 rule_code，否则返回 None。

    tags 是商品的品类标签（fresh/custom/intimate/digital/mattress）；
    opened/activated/seal_removed 是商品当前状态。
    """
    p = policy or get_policy()
    state = {"opened": opened, "activated": activated, "seal_removed": seal_removed}
    for cat in p.special.no_return_categories:
        if cat.kinds:  # 例如已激活的手机、平板
            hit = kind in cat.kinds
        else:
            hit = cat.key in tags
        if not hit:
            continue
        if cat.condition is None:
            return "AS_NOT_RETURNABLE_CATEGORY"
        if state.get(cat.condition, False):
            return _CONDITION_RULE[cat.condition]
    return None


# ---------------------------------------------------------------- 换货
def exchange_block_rule(category: str, times_done: int, policy: Policy | None = None) -> str | None:
    """换货被拒时的 rule_code；可换返回 None。"""
    ex = (policy or get_policy()).return_7day.exchange
    if category not in ex.categories:
        return "AS_EXCHANGE_NOT_SUPPORTED"
    if times_done >= ex.max_times:
        return "AS_EXCHANGE_LIMIT"
    return None


# ---------------------------------------------------------------- 费用
def pickup_fee_fen(policy: Policy | None = None) -> int:
    return fen((policy or get_policy()).return_7day.pickup_fee)


def return_insurance_payout_fen(actual_ship_cost_fen: int, policy: Policy | None = None) -> int:
    """运费险赔付：实际寄回费用，不超过上限（进余额）。"""
    cap = fen((policy or get_policy()).return_7day.return_insurance.max_per_order)
    return max(min(actual_ship_cost_fen, cap), 0)
