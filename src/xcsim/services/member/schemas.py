"""会员服务 member（§3.7）。camelCase、{ok,result,error} 包络、HTTP 200/4xx 混用（看 ok）、
金额为浮点数（元）、时间为 UTC ``...Z``、分页 pageNo/pageSize。"""
from __future__ import annotations

from xcsim.simkit.schema_base import CamelModel, OkEnvelope

ERROR_CODES = {
    "USER_NOT_FOUND": "用户不存在（HTTP 200，ok=false）",
    "GRANT_NOT_ALLOWED": "该模板不允许客服发放",
    "GRANT_LIMIT_EXCEEDED": "超出补偿额度上限",
}


class UserCandidate(CamelModel):
    user_id: str
    masked_name: str
    masked_phone: str
    tier: str


class LookupResult(CamelModel):
    candidates: list[UserCandidate]


class UserInfo(CamelModel):
    user_id: str
    masked_name: str
    masked_phone: str
    masked_email: str
    gender: str
    birthday: str | None = None
    registered_at: str
    tier: str
    status: str  # active / frozen / closed


class AddressOut(CamelModel):
    address_id: str
    receiver: str
    masked_phone: str
    province: str
    city: str
    district: str
    detail: str
    is_remote: bool
    has_elevator: bool
    floor: int | None = None
    is_default: bool


class AddressesResult(CamelModel):
    items: list[AddressOut]


class TierThresholds(CamelModel):
    silver: float
    gold: float


class TierInfo(CamelModel):
    user_id: str
    tier: str
    assessed_at: str
    window_from: str
    window_to: str
    spend_yuan: float
    next_assess_at: str
    thresholds: TierThresholds
    advance_refund_suspended_until: str | None = None


class ExpiringPoints(CamelModel):
    points: int
    expires_at: str


class PointsInfo(CamelModel):
    user_id: str
    available: int
    pending: int  # 签收满 7 天后才到账
    expiring_soon: list[ExpiringPoints]


class LedgerItem(CamelModel):
    txn_id: str
    delta: int
    kind: str  # earn/redeem/refund_return/clawback/expire/birthday_bonus
    order_id: str | None = None
    happened_at: str
    available_at: str | None = None
    expires_at: str | None = None
    balance_after: int


class LedgerPage(CamelModel):
    items: list[LedgerItem]
    page_no: int
    page_size: int
    total: int


class CouponOut(CamelModel):
    coupon_id: str
    template_id: str
    status: str  # unused/locked/used/expired/returned
    kind: str  # platform/category/free_ship/return_ship/compensation
    threshold: float
    amount: float
    valid_from: str
    valid_to: str
    used_order_id: str | None = None
    category_id: str | None = None


class CouponList(CamelModel):
    items: list[CouponOut]


class GrantRequest(CamelModel):
    template_id: str
    ticket_id: str
    reason: str


UserLookupResponse = OkEnvelope[LookupResult]
UserResponse = OkEnvelope[UserInfo]
AddressesResponse = OkEnvelope[AddressesResult]
TierResponse = OkEnvelope[TierInfo]
PointsResponse = OkEnvelope[PointsInfo]
LedgerResponse = OkEnvelope[LedgerPage]
CouponsResponse = OkEnvelope[CouponList]
GrantResponse = OkEnvelope[CouponOut]
