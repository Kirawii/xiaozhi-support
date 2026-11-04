"""支付与退款服务 payment（§3.6）。snake_case、裸 JSON、错误为 RFC 9457 problem+json、
金额为整数分 ``*_fen``、时间为 epoch 毫秒、分页 limit/offset。"""
from __future__ import annotations

from typing import Any, Literal

from xcsim.simkit.schema_base import SnakeModel

ERROR_CODES = {
    "PAYMENT_NOT_FOUND": "订单无支付记录（404）",
    "REFUND_EXCEEDS_REFUNDABLE": "退款金额超过可退余额（422）",
    "REFUND_ALREADY_IN_PROGRESS": "该订单已有进行中的退款（409）",
    "CASE_NOT_APPROVED": "售后单未审批通过（422）",
    "IDEMPOTENCY_KEY_REUSED": "幂等键已用于不同参数（422）",
    "REFUND_NOT_FOUND": "退款不存在（404）",
    "REFUND_NOT_RETRYABLE": "该退款不可重试（409）",
    "ACCOUNT_NOT_FOUND": "账户不存在（404）",
}
FAILURE_CODES = ("CARD_CANCELLED", "ACCOUNT_FROZEN", "CHANNEL_ERROR")
Channel = Literal["mobile_pay_a", "mobile_pay_b", "debit_card", "credit_card", "balance", "points"]
RefundStatus = Literal["reviewing", "processing", "succeeded", "failed", "rejected"]


class ProblemDetail(SnakeModel):
    """RFC 9457 problem+json；code 是机器可读的业务错误码。"""

    type: str
    title: str
    status: int
    detail: str
    code: str
    instance: str
    errors: Any | None = None


class InstallmentPlanOut(SnakeModel):
    plan_id: str
    periods: int
    fee_fen: int
    fee_refundable: bool = False


class PaymentRecordOut(SnakeModel):
    payment_id: str
    order_id: str
    channel: Channel
    channel_display_name: str  # 悦付 / 青禾钱包 / 储蓄卡 / 信用卡 / 星川余额 / 积分
    amount_fen: int
    status: str
    paid_at: int  # epoch 毫秒
    channel_txn_id: str | None = None
    bank_alias: str | None = None
    card_last4: str | None = None
    installment_plan: InstallmentPlanOut | None = None


class PaymentsResponse(SnakeModel):
    order_id: str
    items: list[PaymentRecordOut]


class RefundCreate(SnakeModel):
    order_id: str
    case_id: str | None = None
    amount_fen: int
    reason: str


class RefundOut(SnakeModel):
    refund_id: str
    order_id: str
    case_id: str | None = None
    payment_id: str | None = None
    amount_fen: int
    points_amount: int = 0
    status: RefundStatus
    channel_state: str  # normal / unknown（渠道超时未知）
    reason: str
    idempotency_key: str | None = None
    created_at: int
    reviewed_at: int | None = None
    settled_at: int | None = None
    arrival_eta_from: int | None = None
    arrival_eta_to: int | None = None
    failure_code: str | None = None
    retry_of: str | None = None


class RefundListResponse(SnakeModel):
    items: list[RefundOut]
    limit: int
    offset: int
    total: int


class BalanceResponse(SnakeModel):
    user_id: str
    balance_fen: int
    updated_at: int
