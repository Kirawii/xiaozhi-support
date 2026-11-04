"""订单服务 order（§3.4）。snake_case、{code,message,data,request_id} 包络、金额为元的字符串、时间 ISO +08:00。"""
from __future__ import annotations

from typing import Any

from xcsim.simkit.schema_base import CodeEnvelope, SnakeModel

ERROR_CODES = {
    40001: "参数不合法",
    40401: "订单不存在（含 user_id 不匹配，不泄露他人订单）",
    40901: "非法状态转换",
    40902: "定制商品已开工，不能取消",
    40903: "已发货，不能取消（请走拒收）",
    40904: "已发货，不能改地址",
    40905: "地址已改过一次",
    40906: "生鲜不配送偏远地区",
    40907: "该商品不支持拒收",
}


class ErrorBody(SnakeModel):
    code: int
    message: str
    data: None = None
    request_id: str = ""
    detail: dict[str, Any] | None = None  # 非法转换时给出当前状态和允许的目标状态


class AddressSnapshot(SnakeModel):
    receiver: str
    phone: str  # 打码
    province: str
    city: str
    district: str
    detail: str
    is_remote: bool
    has_elevator: bool
    floor: int | None = None


class OrderListItem(SnakeModel):
    order_id: str
    user_id: str
    status: str
    order_kind: str  # normal / presale / gift_included / custom
    created_at: str
    paid_at: str | None = None
    total_payable: str
    item_count: int


class OrderListData(SnakeModel):
    items: list[OrderListItem]
    page: int
    page_size: int
    total: int


class OrderItemOut(SnakeModel):
    item_id: str
    sku_id: str
    sku_name: str
    qty: int
    unit_price: str
    is_gift: bool
    gift_value: str
    discount_alloc: str  # 分摊到该行的优惠
    paid_amount: str
    item_status: str
    activation_status: str  # n/a / inactive / activated
    seal_status: str  # n/a / sealed / opened


class ItemDiscountOut(SnakeModel):
    item_id: str
    source_type: str  # coupon / full_reduction / points / flash
    source_id: str
    amount: str


class FeeBreakdown(SnakeModel):
    goods_total: str
    coupon_discount: str
    promo_discount: str
    goods_paid: str
    points_used: int
    points_value: str
    shipping_fee: str
    bulky_fee: str
    cold_chain_fee: str
    floor_fee: str
    total_payable: str
    shipping_rule_version: str  # legacy / current


class ParcelRef(SnakeModel):
    mail_no: str
    carrier_code: str
    status: str


class ParcelsSummary(SnakeModel):
    """同步快照，可能落后于物流服务；以物流服务为准。"""

    total: int
    delivered: int
    parcels: list[ParcelRef]


class OrderDetail(SnakeModel):
    order_id: str
    user_id: str
    status: str
    order_kind: str
    created_at: str
    paid_at: str | None = None
    cancelled_at: str | None = None
    completed_at: str | None = None
    tier_at_order: str
    address: AddressSnapshot
    address_change_count: int
    items: list[OrderItemOut]
    discounts: list[ItemDiscountOut]
    fees: FeeBreakdown
    deposit_amount: str | None = None
    balance_due: str | None = None
    parcels_summary: ParcelsSummary
    synced_at: str
    remark: str | None = None


class OrderEvent(SnakeModel):
    event_id: str
    event_type: str
    from_status: str | None = None
    to_status: str
    occurred_at: str
    detail: dict[str, Any] = {}


class OrderEventsData(SnakeModel):
    order_id: str
    items: list[OrderEvent]


class CancelRequest(SnakeModel):
    user_id: str
    reason: str


class CancelData(SnakeModel):
    order_id: str
    status: str
    refund_ref: str | None = None


class AddressIn(SnakeModel):
    receiver: str
    phone: str
    province: str
    city: str
    district: str
    detail: str
    has_elevator: bool = True
    floor: int | None = None


class ChangeAddressRequest(SnakeModel):
    user_id: str
    address: AddressIn


class ChangeAddressData(SnakeModel):
    order_id: str
    address: AddressSnapshot
    fee_delta: str  # 补收（正）或退还（负）的运费差额


class RejectRequest(SnakeModel):
    user_id: str
    reason: str


class RejectData(SnakeModel):
    order_id: str
    status: str
    rejection_fee: str


class ConfirmReceiptRequest(SnakeModel):
    user_id: str


class ConfirmReceiptData(SnakeModel):
    order_id: str
    status: str


OrderListResponse = CodeEnvelope[OrderListData]
OrderDetailResponse = CodeEnvelope[OrderDetail]
OrderEventsResponse = CodeEnvelope[OrderEventsData]
CancelResponse = CodeEnvelope[CancelData]
ChangeAddressResponse = CodeEnvelope[ChangeAddressData]
RejectResponse = CodeEnvelope[RejectData]
ConfirmReceiptResponse = CodeEnvelope[ConfirmReceiptData]
