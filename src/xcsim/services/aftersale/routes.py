"""aftersale 接口桩。WP7 会替换为真实实现（含强校验规则）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from xcsim.simkit.auth import require
from xcsim.simkit.idempotency import idempotency_key
from xcsim.simkit.stub import mark_stub, rid

from . import schemas as s

router = APIRouter(
    prefix="/aftersale/v1",
    tags=["aftersale"],
    dependencies=[Depends(mark_stub)],
    responses={400: {"model": s.ErrorBody}, 401: {"model": s.ErrorBody}, 403: {"model": s.ErrorBody}, 404: {"model": s.ErrorBody}, 409: {"model": s.ErrorBody}, 422: {"model": s.ErrorBody}},
)
READ = [Depends(require("aftersale", "aftersale:read"))]
WRITE = [Depends(require("aftersale", "aftersale:write"))]

_NOW = "2026-11-01T10:00:00+08:00"


def _case(case_id: str = "AS-STUB-1", **kw) -> s.CaseOut:
    base = dict(
        case_id=case_id, order_id="XC2610184821", user_id="U1048213", case_type="return_7day", status="approved",
        reason="不想要了", applied_at=_NOW, policy_version="3.0", delivered_at_used="2026-10-25T14:10:00+08:00",
        deadline_at="2026-11-01T23:59:59+08:00", needs_inspection_report=False, refund_amount="259.00",
        items=[s.CaseItemOut(item_id="XC2610184821-1", qty=1, refund_amount="259.00")],
    )
    base.update(kw)
    return s.CaseOut(**base)


@router.post("/cases", status_code=201, response_model=s.CaseResponse, dependencies=WRITE, summary="提交售后申请（强校验规则）")
def create_case(body: s.CaseCreate, request: Request, key: str = Depends(idempotency_key)) -> s.CaseResponse:
    preview = s.RefundPreview(gross_refund="259.00", fee_deductions=[], net_refund="259.00", needs_manual_review=False)
    case = _case(user_id=body.user_id, order_id=body.order_id, case_type=body.case_type, reason=body.reason, refund_preview=preview)
    return s.CaseResponse(data=case, request_id=rid(request))


@router.get("/cases", response_model=s.CaseListResponse, dependencies=READ, summary="售后单列表")
def list_cases(
    request: Request,
    user_id: str = Query(...),
    order_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> s.CaseListResponse:
    return s.CaseListResponse(data=s.CaseListData(items=[_case(user_id=user_id)], page=page, page_size=page_size, total=1), request_id=rid(request))


@router.get("/cases/{case_id}", response_model=s.CaseDetailResponse, dependencies=READ, summary="售后单详情与时间线")
def get_case(case_id: str, request: Request, user_id: str = Query(...)) -> s.CaseDetailResponse:
    detail = s.CaseDetail(
        **_case(case_id, user_id=user_id).model_dump(),
        timeline=[s.CaseEventOut(event_id="CE1", from_status=None, to_status="submitted", occurred_at=_NOW),
                  s.CaseEventOut(event_id="CE2", from_status="submitted", to_status="approved", occurred_at=_NOW)],
    )
    return s.CaseDetailResponse(data=detail, request_id=rid(request))


@router.post("/cases/{case_id}/withdraw", response_model=s.CaseResponse, dependencies=WRITE, summary="撤销申请（仍计入申请次数）")
def withdraw_case(case_id: str, body: s.WithdrawRequest, request: Request, key: str = Depends(idempotency_key)) -> s.CaseResponse:
    return s.CaseResponse(data=_case(case_id, user_id=body.user_id, status="withdrawn", withdrawn_at=_NOW), request_id=rid(request))


@router.post("/cases/{case_id}/return-shipment", response_model=s.ReturnShipmentResponse, dependencies=WRITE, summary="登记寄回单号（可能触发先行退款）")
def return_shipment(case_id: str, body: s.ReturnShipmentIn, request: Request, key: str = Depends(idempotency_key)) -> s.ReturnShipmentResponse:
    out = s.ReturnShipmentOut(
        case_id=case_id, status="return_shipped", mail_no=body.mail_no, carrier_code=body.carrier_code,
        advance_refund_triggered=False,
    )
    return s.ReturnShipmentResponse(data=out, request_id=rid(request))


@router.post("/price-protection", status_code=201, response_model=s.PriceProtectionResponse, dependencies=WRITE, summary="申请价保")
def price_protection(body: s.PriceProtectionRequest, request: Request, key: str = Depends(idempotency_key)) -> s.PriceProtectionResponse:
    out = s.PriceProtectionOut(
        claim_id="PP-STUB-1", order_id=body.order_id, item_id=body.item_id, day_k=7, window_days=7,
        price_diff="11.00", payout_to="balance", status="approved",
    )
    return s.PriceProtectionResponse(data=out, request_id=rid(request))


def _invoice(**kw) -> s.InvoiceOut:
    base = dict(
        invoice_id="INV-STUB-1", order_id="XC2610184821", user_id="U1048213", title="个人", type="personal",
        amount="259.00", status="issued", issued_at=_NOW, reissue_count=0,
    )
    base.update(kw)
    return s.InvoiceOut(**base)


@router.post("/invoices", status_code=201, response_model=s.InvoiceResponse, dependencies=WRITE, summary="开发票")
def create_invoice(body: s.InvoiceCreate, request: Request, key: str = Depends(idempotency_key)) -> s.InvoiceResponse:
    inv = _invoice(order_id=body.order_id, user_id=body.user_id, title=body.title, tax_no=body.tax_no, type=body.type)
    return s.InvoiceResponse(data=inv, request_id=rid(request))


@router.post("/invoices/{invoice_id}/reissue", status_code=201, response_model=s.InvoiceResponse, dependencies=WRITE, summary="补开发票（限一次）")
def reissue_invoice(invoice_id: str, body: s.InvoiceReissue, request: Request, key: str = Depends(idempotency_key)) -> s.InvoiceResponse:
    return s.InvoiceResponse(data=_invoice(invoice_id="INV-STUB-2", title=body.title, tax_no=body.tax_no, reissue_count=1), request_id=rid(request))


@router.get("/invoices", response_model=s.InvoiceListResponse, dependencies=READ, summary="发票列表")
def list_invoices(request: Request, user_id: str = Query(...), order_id: str = Query(...)) -> s.InvoiceListResponse:
    return s.InvoiceListResponse(data=s.InvoiceListData(items=[_invoice(user_id=user_id, order_id=order_id)]), request_id=rid(request))
