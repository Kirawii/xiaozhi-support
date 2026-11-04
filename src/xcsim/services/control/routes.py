"""control 接口桩。WP8 会替换为真实实现。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from xcsim.simkit.auth import require
from xcsim.simkit.faults import FaultRule
from xcsim.simkit.stub import mark_stub

from . import schemas as s

router = APIRouter(
    prefix="/control/v1",
    tags=["control"],
    dependencies=[Depends(mark_stub), Depends(require("control", "control:admin"))],
    responses={401: {"model": s.ErrorBody}, 403: {"model": s.ErrorBody}, 404: {"model": s.ErrorBody}, 422: {"model": s.ErrorBody}},
)
# 服务间事件上报：只接受 internal token，不进 OpenAPI
report_router = APIRouter(
    prefix="/control/_sim",
    include_in_schema=False,
    dependencies=[Depends(mark_stub), Depends(require("control", "control:report"))],
)

_NOW = "2026-11-01T10:00:00+08:00"
_ANCHOR = "2026-11-01T10:00:00+08:00"
_EVENT = s.EventItem(
    seq=1, sim_time=_NOW, type="refund.succeeded", service="payment", entity_type="refund", entity_id="RF-STUB-1",
    payload={"amount_fen": 25900}, cause_seq=None,
)
_SCENARIO = s.ScenarioSummary(id="RET-01", title="签收第 7 天 23:30 申请无理由退货", category="return_7day", difficulty="medium")


@router.post("/reset", response_model=s.ResetResult, summary="从快照恢复全部数据库、清空故障和事件")
def reset(body: s.ResetRequest) -> s.ResetResult:
    return s.ResetResult(seed=body.seed or 42, profile=body.profile or "clean", restored=["catalog", "order"], duration_ms=1200)


@router.get("/clock", response_model=s.ClockState, summary="读取仿真时钟")
def get_clock() -> s.ClockState:
    return s.ClockState(sim_time=_NOW, mode="frozen", rate=1.0, anchor=_ANCHOR)


@router.post("/clock", response_model=s.ClockState, summary="设置或推进仿真时钟（后退须 allow_backward=true）")
def set_clock(body: s.ClockSetRequest) -> s.ClockState:
    return s.ClockState(sim_time=body.set or _NOW, mode=body.mode or "frozen", rate=body.rate or 1.0, anchor=_ANCHOR)


@router.get("/faults", response_model=s.FaultListResponse, summary="故障规则列表")
def list_faults() -> s.FaultListResponse:
    return s.FaultListResponse(profile="clean", items=[])


@router.post("/faults", status_code=201, response_model=FaultRule, summary="新增故障规则")
def add_fault(body: FaultRule) -> FaultRule:
    return body


@router.delete("/faults/{fault_id}", response_model=s.FaultDeleteResult, summary="删除一条故障规则")
def delete_fault(fault_id: str) -> s.FaultDeleteResult:
    return s.FaultDeleteResult(deleted=1)


@router.delete("/faults", response_model=s.FaultDeleteResult, summary="删除全部故障规则")
def clear_faults() -> s.FaultDeleteResult:
    return s.FaultDeleteResult(deleted=0)


@router.post("/faults/profile", response_model=s.ProfileResult, summary="切换配置档 clean / realistic")
def set_profile(body: s.ProfileRequest) -> s.ProfileResult:
    return s.ProfileResult(profile=body.profile)


@router.post("/events/trigger", status_code=201, response_model=s.EventItem, summary="手动触发业务事件")
def trigger_event(body: s.TriggerEventRequest) -> s.EventItem:
    return _EVENT.model_copy(update={"type": body.type, "entity_id": body.entity_id or "E-STUB-1", "payload": body.params})


@router.get("/events", response_model=s.EventsResponse, summary="事件日志（游标分页，支持长轮询）")
def list_events(
    after_seq: int = Query(0, ge=0),
    types: str | None = Query(None, description="逗号分隔的事件类型"),
    wait_seconds: float = Query(0, ge=0, le=60),
    limit: int = Query(100, ge=1, le=1000),
) -> s.EventsResponse:
    return s.EventsResponse(items=[_EVENT], next_seq=1)


@router.get("/scenarios", response_model=s.ScenarioListResponse, summary="场景列表")
def list_scenarios() -> s.ScenarioListResponse:
    return s.ScenarioListResponse(items=[_SCENARIO])


@router.get("/scenarios/{scenario_id}", response_model=s.ScenarioDetail, summary="场景详情")
def get_scenario(scenario_id: str) -> s.ScenarioDetail:
    return s.ScenarioDetail(
        **_SCENARIO.model_dump() | {"id": scenario_id}, policy_refs=["return_7day.window_days"], kb_docs=["P01-return-7day"],
        user_id="U1048213", order_ids=["XC2610184821"], sim_clock="2026-11-01T23:30:00+08:00", anchor="relative",
        setup={"faults": [], "trigger_events": []}, opening_utterance="这个东西不想要了，今天还能退吗",
        preconditions=[{"call": "GET /order/v1/orders/{order_id}", "path": "$.data.status", "op": "in", "value": ["delivered", "completed"]}],
        expected={"decision": "allow"},
    )


@router.post("/scenarios/{scenario_id}/prepare", response_model=s.PrepareResult, summary="场景隔离准备：重置、拨钟、设故障")
def prepare(scenario_id: str) -> s.PrepareResult:
    return s.PrepareResult(
        scenario_id=scenario_id, sim_time="2026-11-01T23:30:00+08:00", user_id="U1048213",
        order_ids=["XC2610184821"], faults_applied=0,
    )


@router.post("/scenarios/{scenario_id}/verify", response_model=s.VerifyResult, summary="逐条校验场景前置条件")
def verify(scenario_id: str) -> s.VerifyResult:
    return s.VerifyResult(
        scenario_id=scenario_id, passed=True,
        results=[s.PreconditionResult(index=0, call="GET /order/v1/orders/XC2610184821", passed=True, expected=["delivered", "completed"], actual="delivered")],
    )


@router.get("/manifest", response_model=s.Manifest, summary="种子、锚点、数据哈希、版本")
def manifest() -> s.Manifest:
    return s.Manifest(seed=42, anchor=_ANCHOR, data_hash="0" * 64, version="0.1.0", scale=1.0, policy_version="3.0")


@report_router.post("/report", response_model=s.ReportAck)
def report(body: s.ReportedEvent) -> s.ReportAck:
    return s.ReportAck(seq=1)
