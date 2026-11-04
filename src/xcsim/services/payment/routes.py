"""payment 接口桩。WP5 会替换为真实实现。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from xcsim.simkit import fmt
from xcsim.simkit.auth import require
from xcsim.simkit.idempotency import idempotency_key
from xcsim.simkit.stub import mark_stub

from . import schemas as s

PROBLEM = {"model": s.ProblemDetail, "content": {"application/problem+json": {}}}
router = APIRouter(
    prefix="/payment/v1",
    tags=["payment"],
    dependencies=[Depends(mark_stub)],
    responses={400: PROBLEM, 401: PROBLEM, 403: PROBLEM, 404: PROBLEM, 409: PROBLEM, 422: PROBLEM},
)
READ = [Depends(require("payment", "payment:read"))]
WRITE = [Depends(require("payment", "payment:write"))]

PAID_AT_MS = fmt.epoch_ms(datetime(2026, 10, 22, 20, 16, 10, tzinfo=fmt.BJ))  # 示例数据
NOW_MS = fmt.epoch_ms(datetime(2026, 11, 1, 10, 0, 0, tzinfo=fmt.BJ))


def _refund(refund_id: str, order_id: str = "XC2610184821", amount_fen: int = 25900, **kw) -> s.RefundOut:
    base = dict(
        refund_id=refund_id, order_id=order_id, case_id=None, payment_id="PAY-STUB-1", amount_fen=amount_fen,
        status="processing", channel_state="normal", reason="七天无理由", created_at=NOW_MS,
        arrival_eta_from=NOW_MS, arrival_eta_to=NOW_MS + 86_400_000,
    )
    base.update(kw)
    return s.RefundOut(**base)


@router.get("/orders/{order_id}/payments", response_model=s.PaymentsResponse, dependencies=READ, summary="订单支付记录")
def order_payments(order_id: str) -> s.PaymentsResponse:
    item = s.PaymentRecordOut(
        payment_id="PAY-STUB-1", order_id=order_id, channel="mobile_pay_a", channel_display_name="悦付",
        amount_fen=25900, status="paid", paid_at=PAID_AT_MS, channel_txn_id="TXN-STUB-1",
    )
    return s.PaymentsResponse(order_id=order_id, items=[item])


@router.post(
    "/refunds", status_code=202, response_model=s.RefundOut, dependencies=WRITE,
    summary="发起退款（必须带 Idempotency-Key；返回 202，状态为 processing 或 reviewing）",
)
def create_refund(body: s.RefundCreate, key: str = Depends(idempotency_key)) -> s.RefundOut:
    return _refund("RF-STUB-1", body.order_id, body.amount_fen, case_id=body.case_id, reason=body.reason, idempotency_key=key)


@router.get("/refunds/{refund_id}", response_model=s.RefundOut, dependencies=READ, summary="退款详情")
def get_refund(refund_id: str) -> s.RefundOut:
    return _refund(refund_id)


@router.get("/refunds", response_model=s.RefundListResponse, dependencies=READ, summary="退款列表（超时后按 order_id 或 idempotency_key 对账）")
def list_refunds(
    order_id: str | None = Query(None),
    idempotency_key: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> s.RefundListResponse:
    return s.RefundListResponse(items=[_refund("RF-STUB-1")], limit=limit, offset=offset, total=1)


@router.post(
    "/refunds/{refund_id}/retry", status_code=202, response_model=s.RefundOut, dependencies=WRITE,
    summary="重试失败的退款（生成新退款，带 retry_of）",
)
def retry_refund(refund_id: str, key: str = Depends(idempotency_key)) -> s.RefundOut:
    return _refund("RF-STUB-2", retry_of=refund_id, idempotency_key=key)


@router.get("/users/{user_id}/balance", response_model=s.BalanceResponse, dependencies=READ, summary="余额")
def balance(user_id: str) -> s.BalanceResponse:
    return s.BalanceResponse(user_id=user_id, balance_fen=1500, updated_at=NOW_MS)
