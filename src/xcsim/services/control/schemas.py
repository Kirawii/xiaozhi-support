"""控制服务 control（§6.1）。前缀 /control/v1，鉴权头 X-Control-Token。

DESIGN 的 §3.2 风格表没有列 control，这里沿用 ticket 的风格：snake_case 裸 JSON，
HTTP 状态 + {errors:[{code,field,message}]}，时间 ISO +08:00。
"""
from __future__ import annotations

from typing import Any, Literal

from xcsim.simkit.faults import FaultRule
from xcsim.simkit.schema_base import SnakeModel

EVENT_TYPES = (
    "parcel.delivered", "parcel.locker_in", "parcel.stalled", "parcel.lost", "parcel.returned",
    "refund.succeeded", "refund.failed", "refund.review_passed",
    "case.inspection_passed", "case.inspection_failed",
    "ticket.agent_reply", "price.changed", "inventory.changed",
)
ERROR_CODES = {
    "SEED_NOT_GENERATED": "该种子没有生成过快照，不能重置",
    "CLOCK_BACKWARD_NOT_ALLOWED": "后退时钟必须显式 allow_backward=true",
    "CLOCK_OUT_OF_RANGE": "锚点超出合法范围",
    "UNKNOWN_EVENT_TYPE": "未知的事件类型",
    "SCENARIO_NOT_FOUND": "场景不存在",
    "FAULT_NOT_FOUND": "故障规则不存在",
}


class ErrorItem(SnakeModel):
    code: str
    field: str | None = None
    message: str


class ErrorBody(SnakeModel):
    errors: list[ErrorItem]


# ---- reset / clock
class ResetRequest(SnakeModel):
    seed: int | None = None
    profile: Literal["clean", "realistic"] | None = None


class ResetResult(SnakeModel):
    seed: int
    profile: str
    restored: list[str]
    duration_ms: int


class ClockState(SnakeModel):
    sim_time: str
    mode: Literal["frozen", "running"]
    rate: float
    anchor: str
    warning: str | None = None  # 后退时钟时提示“全局列表可能看到未来数据”


class ClockSetRequest(SnakeModel):
    set: str | None = None  # 绝对时间 ISO
    advance_seconds: float | None = None
    allow_backward: bool = False
    mode: Literal["frozen", "running"] | None = None
    rate: float | None = None


# ---- faults
class FaultListResponse(SnakeModel):
    profile: str
    items: list[FaultRule]


class FaultDeleteResult(SnakeModel):
    deleted: int


class ProfileRequest(SnakeModel):
    profile: Literal["clean", "realistic"]


class ProfileResult(SnakeModel):
    profile: str


# ---- events
class TriggerEventRequest(SnakeModel):
    type: str
    entity_id: str | None = None
    params: dict[str, Any] = {}


class EventItem(SnakeModel):
    seq: int
    sim_time: str
    type: str
    service: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = {}
    cause_seq: int | None = None


class EventsResponse(SnakeModel):
    items: list[EventItem]
    next_seq: int  # 下次请求的 after_seq


# ---- scenarios
class ScenarioSummary(SnakeModel):
    id: str
    title: str
    category: str
    difficulty: str


class ScenarioListResponse(SnakeModel):
    items: list[ScenarioSummary]


class ScenarioDetail(ScenarioSummary):
    policy_refs: list[str]
    kb_docs: list[str]
    user_id: str
    order_ids: list[str]
    sim_clock: str
    anchor: Literal["relative", "absolute"]
    setup: dict[str, Any]
    opening_utterance: str
    preconditions: list[dict[str, Any]]
    expected: dict[str, Any]


class PrepareResult(SnakeModel):
    scenario_id: str
    sim_time: str
    user_id: str
    order_ids: list[str]
    faults_applied: int
    warning: str | None = None


class PreconditionResult(SnakeModel):
    index: int
    call: str
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str | None = None


class VerifyResult(SnakeModel):
    scenario_id: str
    passed: bool
    results: list[PreconditionResult]


class Manifest(SnakeModel):
    seed: int
    anchor: str
    data_hash: str
    version: str
    scale: float
    policy_version: str


# ---- 内部上报（不进 OpenAPI）
class ReportedEvent(SnakeModel):
    type: str
    service: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = {}
    cause_seq: int | None = None
    sim_time: str | None = None


class ReportAck(SnakeModel):
    seq: int
