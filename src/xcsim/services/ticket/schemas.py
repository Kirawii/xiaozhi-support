"""工单服务 ticket（§3.9）。snake_case、裸 JSON、错误为 {errors:[{code,field,message}]}、
无金额、时间 ISO +08:00、分页 page/per_page。"""
from __future__ import annotations

from typing import Any

from xcsim.simkit.schema_base import SnakeModel

ERROR_CODES = {
    "INVALID_CATEGORY": "工单分类不合法",
    "SUMMARY_REQUIRED": "必须填写摘要",
    "TICKET_NOT_FOUND": "工单不存在",
    "TICKET_CLOSED": "工单已关闭，不能追加消息",
    "ILLEGAL_TRANSITION": "非法状态转换，detail 给出当前状态与允许的目标状态",
}


class ErrorItem(SnakeModel):
    code: str
    field: str | None = None
    message: str


class ErrorBody(SnakeModel):
    errors: list[ErrorItem]


class TicketCreate(SnakeModel):
    user_id: str
    order_id: str | None = None
    category: str
    summary: str
    handoff_context: dict[str, Any] = {}


class MessageOut(SnakeModel):
    message_id: str
    ticket_id: str
    sender: str  # customer / cs_bot / human_agent / system
    text: str
    created_at: str
    is_internal: bool = False


class TicketOut(SnakeModel):
    ticket_id: str
    user_id: str
    order_id: str | None = None
    case_id: str | None = None
    channel: str
    category: str
    priority: str
    status: str
    summary: str
    handoff_context: dict[str, Any] = {}
    created_at: str
    first_response_due_at: str | None = None
    first_responded_at: str | None = None
    resolved_at: str | None = None
    agent_id: str | None = None
    queue_position: int | None = None
    estimated_wait_minutes: int | None = None
    in_service_hours: bool  # 当前是否在人工客服在线时段


class TicketDetail(TicketOut):
    messages: list[MessageOut]


class TicketListResponse(SnakeModel):
    items: list[TicketOut]
    page: int
    per_page: int
    total: int


class MessageCreate(SnakeModel):
    sender: str
    text: str


class MessageListResponse(SnakeModel):
    items: list[MessageOut]


class QueueResponse(SnakeModel):
    tier: str
    waiting: int
    estimated_wait_minutes: int
    in_service_hours: bool
