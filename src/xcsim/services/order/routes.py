"""order 接口桩。WP3 会替换为真实实现。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from xcsim.simkit.auth import require
from xcsim.simkit.idempotency import idempotency_key
from xcsim.simkit.stub import mark_stub, rid

from . import schemas as s

router = APIRouter(
    prefix="/order/v1",
    tags=["order"],
    dependencies=[Depends(mark_stub)],
    responses={400: {"model": s.ErrorBody}, 401: {"model": s.ErrorBody}, 403: {"model": s.ErrorBody}, 404: {"model": s.ErrorBody}, 409: {"model": s.ErrorBody}},
)
READ = [Depends(require("order", "order:read"))]
WRITE = [Depends(require("order", "order:write"))]

_ADDR = s.AddressSnapshot(
    receiver="陈**", phone="100****8213", province="广东", city="广州", district="天河区",
    detail="示例路 1 号", is_remote=False, has_elevator=True, floor=6,
)
_FEES = s.FeeBreakdown(
    goods_total="259.00", coupon_discount="0.00", promo_discount="0.00", goods_paid="259.00", points_used=0,
    points_value="0.00", shipping_fee="0.00", bulky_fee="0.00", cold_chain_fee="0.00", floor_fee="0.00",
    total_payable="259.00", shipping_rule_version="current",
)
_ITEM = s.OrderItemOut(
    item_id="XC2610184821-1", sku_id="SKU-N12-128-BLK", sku_name="星川 N12 智能手机 128GB 黑色", qty=1,
    unit_price="259.00", is_gift=False, gift_value="0.00", discount_alloc="0.00", paid_amount="259.00",
    item_status="delivered", activation_status="inactive", seal_status="sealed",
)


def _detail(order_id: str) -> s.OrderDetail:
    return s.OrderDetail(
        order_id=order_id, user_id="U1048213", status="delivered", order_kind="normal",
        created_at="2026-10-22T20:15:00+08:00", paid_at="2026-10-22T20:16:10+08:00", tier_at_order="normal",
        address=_ADDR, address_change_count=0, items=[_ITEM], discounts=[], fees=_FEES,
        parcels_summary=s.ParcelsSummary(total=1, delivered=1, parcels=[s.ParcelRef(mail_no="QL20261025001", carrier_code="QLSY", status="delivered")]),
        synced_at="2026-10-25T14:30:00+08:00",
    )


@router.get("/orders", response_model=s.OrderListResponse, dependencies=READ, summary="订单列表")
def list_orders(
    request: Request,
    user_id: str = Query(...),
    status: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> s.OrderListResponse:
    item = s.OrderListItem(
        order_id="XC2610184821", user_id=user_id, status="delivered", order_kind="normal",
        created_at="2026-10-22T20:15:00+08:00", paid_at="2026-10-22T20:16:10+08:00", total_payable="259.00", item_count=1,
    )
    return s.OrderListResponse(data=s.OrderListData(items=[item], page=page, page_size=page_size, total=1), request_id=rid(request))


@router.get("/orders/{order_id}", response_model=s.OrderDetailResponse, dependencies=READ, summary="订单详情")
def get_order(order_id: str, request: Request, user_id: str = Query(...)) -> s.OrderDetailResponse:
    return s.OrderDetailResponse(data=_detail(order_id), request_id=rid(request))


@router.post("/orders/{order_id}/cancel", response_model=s.CancelResponse, dependencies=WRITE, summary="取消订单")
def cancel_order(order_id: str, body: s.CancelRequest, request: Request, key: str = Depends(idempotency_key)) -> s.CancelResponse:
    return s.CancelResponse(data=s.CancelData(order_id=order_id, status="cancelled", refund_ref="R-STUB-1"), request_id=rid(request))


@router.post("/orders/{order_id}/address", response_model=s.ChangeAddressResponse, dependencies=WRITE, summary="修改收货地址（限一次）")
def change_address(order_id: str, body: s.ChangeAddressRequest, request: Request, key: str = Depends(idempotency_key)) -> s.ChangeAddressResponse:
    a = body.address
    new = s.AddressSnapshot(
        receiver=a.receiver, phone=a.phone, province=a.province, city=a.city, district=a.district,
        detail=a.detail, is_remote=False, has_elevator=a.has_elevator, floor=a.floor,
    )
    return s.ChangeAddressResponse(data=s.ChangeAddressData(order_id=order_id, address=new, fee_delta="0.00"), request_id=rid(request))


@router.post("/orders/{order_id}/reject", response_model=s.RejectResponse, dependencies=WRITE, summary="拒收登记")
def reject_order(order_id: str, body: s.RejectRequest, request: Request, key: str = Depends(idempotency_key)) -> s.RejectResponse:
    return s.RejectResponse(data=s.RejectData(order_id=order_id, status="rejected", rejection_fee="8.00"), request_id=rid(request))


@router.post("/orders/{order_id}/confirm-receipt", response_model=s.ConfirmReceiptResponse, dependencies=WRITE, summary="确认收货（自然幂等，无需幂等键）")
def confirm_receipt(order_id: str, body: s.ConfirmReceiptRequest, request: Request) -> s.ConfirmReceiptResponse:
    return s.ConfirmReceiptResponse(data=s.ConfirmReceiptData(order_id=order_id, status="completed"), request_id=rid(request))


@router.get("/orders/{order_id}/events", response_model=s.OrderEventsResponse, dependencies=READ, summary="订单状态历史")
def order_events(order_id: str, request: Request, user_id: str = Query(...)) -> s.OrderEventsResponse:
    events = [
        s.OrderEvent(event_id="E1", event_type="paid", from_status="pending_payment", to_status="paid", occurred_at="2026-10-22T20:16:10+08:00"),
        s.OrderEvent(event_id="E2", event_type="delivered", from_status="shipped", to_status="delivered", occurred_at="2026-10-25T14:10:00+08:00"),
    ]
    return s.OrderEventsResponse(data=s.OrderEventsData(order_id=order_id, items=events), request_id=rid(request))
