"""policy.yaml 对应的冻结 Pydantic 模型。

金额字段单位为元（Decimal），换算成分请用 ``fen()``。
extra="forbid"：policy.yaml 写错键名会立刻报错。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

Yuan = Decimal


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class Meta(_M):
    version: str
    timezone: str
    cutover_date: date


class ReturnInsurance(_M):
    max_per_order: Yuan
    payout_within_hours: int
    payout_to: str


class Exchange(_M):
    categories: tuple[str, ...]
    max_times: int


class Return7Day(_M):
    window_days: int
    window_start: str
    deadline_time: str
    holiday_extension: bool
    delivery_time_basis: str
    max_applications_per_order_item: int
    pickup_fee: Yuan
    return_insurance: ReturnInsurance
    inspection_hours: int
    manual_review_business_days: int
    exchange: Exchange


class QualityTier(_M):
    from_day: int
    to_day: int | None
    options: tuple[str, ...]
    refund_if_exchange_out_of_stock: bool = False


class InspectionReport(_M):
    digital_unit_price_over: Yuan
    appliance_kinds: tuple[str, ...]


class Quality(_M):
    day_basis: str
    tiers: tuple[QualityTier, ...]
    wrong_or_missing_hours: int
    inspection_report: InspectionReport
    self_ship_compensation_cap: Yuan
    refund_within_hours_after_confirmed: int


class AdvanceRefund(_M):
    tier: str
    max_amount: Yuan
    requires: str
    suspension_days_on_inspect_fail: int


class ArrivalDays(_M):
    min: int
    max: int


class ChannelArrival(_M):
    balance: ArrivalDays
    mobile_pay: ArrivalDays
    debit_card: ArrivalDays
    credit_card: ArrivalDays


class Refund(_M):
    manual_review_over: Yuan
    advance_refund: AdvanceRefund
    channel_arrival_business_days: ChannelArrival
    installment_fee_refundable: bool
    points_clawback_rate: int
    threshold_basis: str


class FeeRule(_M):
    free_over: Yuan
    fee: Yuan


class Bulky(_M):
    fee_per_item: Yuan
    remote_fee_per_item: Yuan


class Fresh(_M):
    cold_chain_fee: Yuan
    free_over: Yuan
    deliver_to_remote: bool


class ShippingCurrent(_M):
    standard: FeeRule
    remote: FeeRule
    remote_provinces: tuple[str, ...]
    bulky: Bulky
    fresh: Fresh


class ShippingLegacy(_M):
    applies_when: str
    standard: FeeRule


class ShippingMember(_M):
    silver_free_ship_coupons_per_month: int
    gold_standard_free: bool
    gold_bulky_discount: Decimal
    cold_chain_member_discount: bool


class Shipping(_M):
    current: ShippingCurrent
    legacy: ShippingLegacy
    member: ShippingMember


class FloorFee(_M):
    free_up_to_floor: int
    per_floor: Yuan


class EtaDays(_M):
    same_province: tuple[int, int]
    cross_province: tuple[int, int]
    remote: tuple[int, int]
    bulky: tuple[int, int]


class Delivery(_M):
    same_day_cutoff: str
    bulky_ship_within_days: int
    eta_days: EtaDays
    floor_fee: FloorFee
    old_mattress_removal_fee: Yuan


class Warranty(_M):
    start: str
    default_months: int
    refurbished_months: int
    replace_after_failed_repairs: int
    part_warranty_min_days: int
    paid_repair_part_warranty_days: int


class TierThresholds(_M):
    silver: Yuan
    gold: Yuan


class Multipliers(_M):
    normal: Decimal
    silver: Decimal
    gold: Decimal


class BirthdayMultipliers(_M):
    silver: Decimal
    gold: Decimal


class Points(_M):
    multiplier: Multipliers
    birthday_multiplier: BirthdayMultipliers
    credit_after_delivery_days: int
    redeem_rate: int
    redeem_cap_ratio: Decimal
    expiry: str


class GoldReturnCoupons(_M):
    per_month: int
    max_each: Yuan


class Membership(_M):
    tiers: TierThresholds
    lookback_months: int
    reassess_day_of_month: int
    points: Points
    gold_return_shipping_coupons: GoldReturnCoupons


class WindowDays(_M):
    normal: int
    silver: int
    gold: int


class PriceProtection(_M):
    window_days: WindowDays
    day_one: str
    excluded_price_types: tuple[str, ...]
    excluded_order_kinds: tuple[str, ...]
    excluded_items: tuple[str, ...]
    max_per_order_item: int
    payout_to: str


class RejectionFee(_M):
    standard_per_order: Yuan
    bulky_per_item: Yuan


class OrderRules(_M):
    unpaid_auto_cancel_minutes: int
    address_change_max_times: int
    rejection_fee: RejectionFee
    custom_cancel_within_hours: int
    presale_deposit_refundable: bool


class NoReturnCategory(_M):
    key: str
    condition: str | None = None
    kinds: tuple[str, ...] = ()


class FreshClaim(_M):
    claim_within_hours: int
    partial_refund_up_to_ratio: Decimal
    cold_chain_refundable_only_on_full_refund: bool


class Special(_M):
    no_return_categories: tuple[NoReturnCategory, ...]
    fresh: FreshClaim


class Invoice(_M):
    apply_within_days_after_completion: int
    completion_after_delivery_days: int
    reissue_max_times: int


class LegacyReturn(_M):
    applies_when: str
    window_days: int


class LegacyPriceProtection(_M):
    window_days: WindowDays
    status: str


class HumanHours(_M):
    open: str
    close: str


class ServiceHours(_M):
    human: HumanHours


class PaymentChannels(_M):
    mobile_pay_a: str
    mobile_pay_b: str
    debit_card: str
    credit_card: str
    balance: str
    points: str


class CouponStacking(_M):
    platform: int
    category: int
    free_ship_stackable: bool


class FirstResponse(_M):
    gold: int
    silver: int
    normal: int


class CsAuthority(_M):
    compensation_coupon_max_per_ticket: Yuan
    compensation_coupon_max_per_user_month: int
    allowed_templates: tuple[str, ...]
    ticket_first_response_minutes: FirstResponse


class LogisticsException(_M):
    no_update_alert_hours: int
    lost_declare_after_hours: int
    disputed_delivery_investigate_hours: int


class Holiday(_M):
    from_: date = Field(alias="from")
    to: date


class BusinessCalendar(_M):
    weekend_days: tuple[int, ...]
    holidays: tuple[Holiday, ...]
    adjusted_workdays: tuple[date, ...]


class Policy(_M):
    meta: Meta
    return_7day: Return7Day
    quality: Quality
    refund: Refund
    shipping: Shipping
    delivery: Delivery
    warranty: Warranty
    membership: Membership
    price_protection: PriceProtection
    order: OrderRules
    special: Special
    invoice: Invoice
    legacy_return: LegacyReturn
    legacy_price_protection: LegacyPriceProtection
    service_hours: ServiceHours
    payment_channels: PaymentChannels
    coupon_stacking: CouponStacking
    cs_authority: CsAuthority
    logistics_exception: LogisticsException
    business_calendar: BusinessCalendar


def fen(yuan: Decimal | int | str) -> int:
    """元 -> 整数分（Decimal 运算，不经过浮点）。"""
    value = Decimal(str(yuan)) * 100
    if value != value.to_integral_value():
        raise ValueError(f"金额不是整数分: {yuan}")
    return int(value)
