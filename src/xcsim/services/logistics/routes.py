"""logistics 接口桩。WP4 会替换为真实实现。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from xcsim.simkit.auth import require
from xcsim.simkit.stub import mark_stub

from . import schemas as s

router = APIRouter(prefix="/logistics/v1", tags=["logistics"], dependencies=[Depends(mark_stub)])
READ = [Depends(require("logistics", "logistics:read"))]
WRITE = [Depends(require("logistics", "logistics:write"))]

_TRACES = [
    s.TraceItem(op_code=40, op_desc="已签收，签收人：本人", op_site="广州天河站", operator="快递员 A", accept_time="2026-10-25 14:10:00"),
    s.TraceItem(op_code=30, op_desc="派送中", op_site="广州天河站", operator="快递员 A", accept_time="2026-10-25 09:20:00"),
    s.TraceItem(op_code=20, op_desc="到达广州转运中心", op_site="广州转运中心", accept_time="2026-10-24 22:05:00"),
    s.TraceItem(op_code=10, op_desc="已揽收", op_site="广州仓", accept_time="2026-10-23 18:40:00"),
]


@router.get("/trace", response_model=s.TraceResponse, dependencies=READ, summary="物流轨迹")
def trace(
    mail_no: str = Query(..., alias="mailNo"),
    carrier_code: str = Query(..., alias="carrierCode"),
) -> s.TraceResponse:
    return s.TraceResponse(data=s.TraceData(mail_no=mail_no, carrier_code=carrier_code, current_status="40", traces=_TRACES))


@router.get("/parcels", response_model=s.ParcelsResponse, dependencies=READ, summary="订单包裹列表")
def parcels(order_no: str = Query(..., alias="orderNo")) -> s.ParcelsResponse:
    return s.ParcelsResponse(
        data=s.ParcelsData(order_no=order_no, parcels=[s.ParcelBrief(mail_no="QL20261025001", carrier_code="QLSY", current_status="40")])
    )


@router.get("/parcels/{mail_no}/sign-info", response_model=s.SignInfoResponse, dependencies=READ, summary="签收信息")
def sign_info(mail_no: str, carrier_code: str = Query(..., alias="carrierCode")) -> s.SignInfoResponse:
    return s.SignInfoResponse(
        data=s.SignInfoData(mail_no=mail_no, sign_time="2026-10-25 14:10:00", sign_type="hand", signer="本人")
    )


@router.post("/incidents", response_model=s.IncidentResponse, dependencies=WRITE, summary="报障（按 mailNo+kind 自然幂等）")
def create_incident(body: s.IncidentCreate) -> s.IncidentResponse:
    return s.IncidentResponse(
        data=s.IncidentData(
            incident_no="INC-STUB-1", mail_no=body.mail_no, kind=body.kind, status="open", opened_at="2026-11-01 10:00:00"
        )
    )


@router.get("/incidents/{incident_no}", response_model=s.IncidentResponse, dependencies=READ, summary="报障单状态")
def get_incident(incident_no: str) -> s.IncidentResponse:
    return s.IncidentResponse(
        data=s.IncidentData(
            incident_no=incident_no, mail_no="QL20261025001", kind="stalled", status="open", opened_at="2026-11-01 10:00:00"
        )
    )


@router.get("/carriers", response_model=s.CarriersResponse, dependencies=READ, summary="承运商与状态编码表")
def carriers() -> s.CarriersResponse:
    return s.CarriersResponse(
        data=s.CarriersData(
            carriers=[
                s.CarrierInfo(carrier_code="QLSY", name="青鸾速运", usage="普通件", status="normal",
                              status_codes={"10": "揽收", "20": "运输", "30": "派送", "35": "入柜", "40": "签收", "50": "异常", "60": "退回"}),
                s.CarrierInfo(carrier_code="XNDJ", name="玄鸟大件", usage="大件", status="normal",
                              status_codes={"已揽件": "已揽件", "运输中": "运输中", "预约中": "预约中", "已送达": "已送达", "配送异常": "配送异常"}),
                s.CarrierInfo(carrier_code="SLLY", name="霜链冷运", usage="生鲜", status="normal",
                              status_codes={"1": "揽收", "2": "运输", "3": "派送", "8": "签收", "9": "异常"}),
            ]
        )
    )
