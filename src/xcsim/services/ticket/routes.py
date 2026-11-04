"""ticket 接口桩。WP6 会替换为真实实现。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from xcsim.simkit.auth import Principal, require
from xcsim.simkit.idempotency import idempotency_key
from xcsim.simkit.stub import mark_stub

from . import schemas as s

router = APIRouter(
    prefix="/ticket/v1",
    tags=["ticket"],
    dependencies=[Depends(mark_stub)],
    responses={400: {"model": s.ErrorBody}, 401: {"model": s.ErrorBody}, 403: {"model": s.ErrorBody}, 404: {"model": s.ErrorBody}, 409: {"model": s.ErrorBody}, 422: {"model": s.ErrorBody}},
)
READ = [Depends(require("ticket", "ticket:read"))]
WRITE = [Depends(require("ticket", "ticket:write"))]
_NOW = "2026-11-01T10:00:00+08:00"


def _ticket(ticket_id: str = "T-STUB-1", **kw) -> s.TicketOut:
    base = dict(
        ticket_id=ticket_id, user_id="U1048213", order_id="XC2610184821", channel="bot_handoff", category="refund",
        priority="normal", status="queued", summary="用户要求人工处理退款", handoff_context={}, created_at=_NOW,
        first_response_due_at="2026-11-01T10:30:00+08:00", queue_position=3, estimated_wait_minutes=15,
        in_service_hours=True,
    )
    base.update(kw)
    return s.TicketOut(**base)


def _msg(public_only: bool = True) -> list[s.MessageOut]:
    msgs = [s.MessageOut(message_id="M1", ticket_id="T-STUB-1", sender="customer", text="我要退款", created_at=_NOW)]
    if not public_only:
        msgs.append(s.MessageOut(message_id="M2", ticket_id="T-STUB-1", sender="human_agent", text="内部备注示例", created_at=_NOW, is_internal=True))
    return msgs


@router.post("/tickets", status_code=201, response_model=s.TicketOut, dependencies=WRITE, summary="创建工单（转人工）")
def create_ticket(body: s.TicketCreate, key: str = Depends(idempotency_key)) -> s.TicketOut:
    return _ticket(user_id=body.user_id, order_id=body.order_id, category=body.category, summary=body.summary, handoff_context=body.handoff_context)


@router.get("/tickets/{ticket_id}", response_model=s.TicketDetail, dependencies=READ, summary="工单详情")
def get_ticket(ticket_id: str) -> s.TicketDetail:
    return s.TicketDetail(**_ticket(ticket_id).model_dump(), messages=_msg())


@router.get("/tickets", response_model=s.TicketListResponse, dependencies=READ, summary="工单列表")
def list_tickets(
    user_id: str = Query(...),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> s.TicketListResponse:
    return s.TicketListResponse(items=[_ticket(user_id=user_id)], page=page, per_page=per_page, total=1)


@router.post("/tickets/{ticket_id}/messages", status_code=201, response_model=s.MessageOut, dependencies=WRITE, summary="追加消息")
def add_message(ticket_id: str, body: s.MessageCreate, key: str = Depends(idempotency_key)) -> s.MessageOut:
    return s.MessageOut(message_id="M-STUB-1", ticket_id=ticket_id, sender=body.sender, text=body.text, created_at=_NOW)


@router.get("/tickets/{ticket_id}/messages", response_model=s.MessageListResponse, summary="消息列表（内部备注仅 internal token 可见）")
def list_messages(
    ticket_id: str,
    after_id: str | None = Query(None),
    principal: Principal = Depends(require("ticket", "ticket:read")),
) -> s.MessageListResponse:
    return s.MessageListResponse(items=_msg(public_only=not principal.has("ticket:internal_notes")))


@router.post("/tickets/{ticket_id}/cancel", response_model=s.TicketOut, dependencies=WRITE, summary="取消工单")
def cancel_ticket(ticket_id: str, key: str = Depends(idempotency_key)) -> s.TicketOut:
    return _ticket(ticket_id, status="cancelled", queue_position=None, estimated_wait_minutes=None)


@router.get("/queue", response_model=s.QueueResponse, dependencies=READ, summary="当前排队状况")
def queue(tier: str = Query("normal")) -> s.QueueResponse:
    return s.QueueResponse(tier=tier, waiting=3, estimated_wait_minutes=15, in_service_hours=True)
