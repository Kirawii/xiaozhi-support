"""售后服务 aftersale（§3.8）。snake_case、{code,message,data,request_id} 包络、金额为元的字符串、时间 ISO +08:00。

写接口强校验规则（D2）：被规则拒绝时返回 HTTP 422，code 为 42200 段，
并带机器可读的 rule_code 和 detail（deadline_at / policy_key / policy_version）。
"""
from __future__ import annotations

from typing import Any, Literal

from xcsim.simkit.schema_base import CodeEnvelope, SnakeModel

CaseType = Literal["return_7day", "quality_return", "exchange", "repair", "fresh_claim", "wrong_missing", "reject_return"]

RULE_CODES = (
    "AS_WINDOW_EXPIRED", "AS_APPLY_LIMIT", "AS_NOT_RETURNABLE_CATEGORY", "AS_ACTIVATED", "AS_OPENED",
    "AS_SEAL_REMOVED", "AS_DETAIL_OVERRIDE", "AS_REFUND_NOT_ALLOWED_TIER", "AS_FRESH_CLAIM_EXPIRED",
    "AS_EXCHANGE_LIMIT", "AS_EXCHANGE_NOT_SUPPORTED", "AS_EVIDENCE_REQUIRED", "AS_INSPECTION_REPORT_REQUIRED",
    "AS_NOT_DELIVERED",
)
ERROR_CODES = {
    40411: "售后单不存在",
    40911: "售后单当前状态不能撤销",
    42200: "规则拒绝（见 rule_code）",
    42211: "寄回单号无效",
    "42221-42226": "价保拒绝",
    "42231-42233": "开票拒绝",
    42234: "补开发票拒绝",
    50301: "下游服务不可用（DEPENDENCY_UNAVAILABLE）",
}


class RuleViolationDetail(SnakeModel):
    deadline_at: str | None = None
    policy_key: str
    policy_version: str


class ErrorBody(SnakeModel):
    code: int
    message: str
    data: None = None
    request_id: str = ""
    rule_code: str | None = None
    detail: RuleViolationDetail | dict[str, Any] | None = None


class CaseItemIn(SnakeModel):
    item_id: str
    qty: int = 1


class ClaimedCondition(SnakeModel):
    """用户自述的商品状态（服务端以订单数据为准校验，不信任此处）。"""

    opened: bool | None = None
    activated: bool | None = None
    seal_removed: bool | None = None
    gift_missing: bool | None = None
    bad_fruit_ratio: str | None = None  # 生鲜坏果率，如 "0.20"


class CaseCreate(SnakeModel):
    user_id: str
    order_id: str
    items: list[CaseItemIn]
    case_type: CaseType
    reason: str
    reason_detail: str | None = None
    evidence_urls: list[str] = []
    claimed_condition: ClaimedCondition = ClaimedCondition()


class FeeDeduction(SnakeModel):
    kind: str  # pickup_fee / gift_missing / ...
    amount: str


class RefundPreview(SnakeModel):
    gross_refund: str  # 扣费前（复核门槛按它判断）
    fee_deductions: list[FeeDeduction]
    net_refund: str
    needs_manual_review: bool


class CaseItemOut(SnakeModel):
    item_id: str
    qty: int
    refund_amount: str


class CaseOut(SnakeModel):
    case_id: str
    order_id: str
    user_id: str
    case_type: CaseType
    status: str
    reason: str
    reason_detail: str | None = None
    evidence_urls: list[str] = []
    applied_at: str
    policy_version: str
    delivered_at_used: str  # 业务签收时间（代收用入柜/入站时间）
    deadline_at: str | None = None
    needs_inspection_report: bool
    refund_amount: str
    fee_deductions: list[FeeDeduction] = []
    withdrawn_at: str | None = None
    items: list[CaseItemOut] = []
    refund_preview: RefundPreview | None = None


class CaseEventOut(SnakeModel):
    event_id: str
    from_status: str | None = None
    to_status: str
    occurred_at: str
    note: str | None = None


class CaseDetail(CaseOut):
    timeline: list[CaseEventOut]


class CaseListData(SnakeModel):
    items: list[CaseOut]
    page: int
    page_size: int
    total: int


class WithdrawRequest(SnakeModel):
    user_id: str


class ReturnShipmentIn(SnakeModel):
    mail_no: str
    carrier_code: str


class ReturnShipmentOut(SnakeModel):
    case_id: str
    status: str
    mail_no: str
    carrier_code: str
    advance_refund_triggered: bool  # 金星且 <=500 元时先行退款
    refund_id: str | None = None


class PriceProtectionRequest(SnakeModel):
    user_id: str
    order_id: str
    item_id: str


class PriceProtectionOut(SnakeModel):
    claim_id: str
    order_id: str
    item_id: str
    day_k: int  # 付款当日为第 1 天
    window_days: int
    price_diff: str
    payout_to: str  # balance
    status: str


class InvoiceCreate(SnakeModel):
    user_id: str
    order_id: str
    title: str
    tax_no: str | None = None
    type: Literal["personal", "company"]


class InvoiceReissue(SnakeModel):
    title: str
    tax_no: str | None = None


class InvoiceOut(SnakeModel):
    invoice_id: str
    order_id: str
    user_id: str
    title: str
    tax_no: str | None = None
    type: str
    amount: str
    status: str
    issued_at: str
    reissue_count: int


class InvoiceListData(SnakeModel):
    items: list[InvoiceOut]


CaseResponse = CodeEnvelope[CaseOut]
CaseDetailResponse = CodeEnvelope[CaseDetail]
CaseListResponse = CodeEnvelope[CaseListData]
ReturnShipmentResponse = CodeEnvelope[ReturnShipmentOut]
PriceProtectionResponse = CodeEnvelope[PriceProtectionOut]
InvoiceResponse = CodeEnvelope[InvoiceOut]
InvoiceListResponse = CodeEnvelope[InvoiceListData]
