"""业务规则唯一入口：加载 policy.yaml，并提供纯函数规则。"""
from .allocation import allocate_discount, item_refund_fen
from .loader import default_policy_path, get_policy, load_policy
from .membership import (
    clawback_points,
    earn_points,
    is_birthday_month,
    max_redeemable_points,
    next_assessment_at,
    points_available_at,
    points_expiry,
    points_multiplier,
    points_to_fen,
    tier_for_spend,
    tier_window,
)
from .models import Policy, fen
from .protection import (
    can_issue_invoice,
    completion_time,
    in_warranty,
    invoice_deadline,
    paid_repair_warranty_end,
    part_warranty_end,
    price_protection,
    repair_part_warranty_end,
    price_protection_window_days,
    replacement_due,
    warranty_end,
    warranty_months,
)
from .quality import (
    fresh_refund,
    needs_inspection_report,
    quality_options,
    quality_tier,
    within_fresh_claim,
    within_wrong_or_missing,
)
from .refund import (
    advance_refund_decision,
    arrival_eta,
    clawback_shortfall_deduction_fen,
    manual_review_due_at,
    needs_manual_review,
    points_refund_amount,
    suspension_until,
)
from .returns import (
    can_apply_again,
    exchange_block_rule,
    is_return_window_open,
    pickup_fee_fen,
    remaining_applications,
    return_block_rule,
    return_insurance_payout_fen,
    return_window,
)
from .shipping import (
    FreshNotDeliverable,
    ShipLine,
    address_change_fee_delta_fen,
    calc_shipping,
    floor_fee_fen,
    is_remote_province,
    shipping_version,
)
from .timeutil import (
    add_business_days,
    bj,
    business_signed_at,
    day_k_after,
    day_k_from_payment,
    in_service_hours,
    is_business_day,
    to_bj,
)

__all__ = [n for n in dir() if not n.startswith("_")]
