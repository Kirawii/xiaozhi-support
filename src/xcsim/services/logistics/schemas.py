"""物流服务 logistics（§3.5）。camelCase 缩写；永远 HTTP 200，看 success/errCode；
金额为数字（元）；时间为北京时间字符串 "2026-10-25 14:03:22"；鉴权头 X-Express-Token。

三家虚构承运商的状态表达不同：
- QLSY 青鸾速运：数字 opCode（10 揽收、20 运输、30 派送、35 入柜、40 签收、50 异常、60 退回），时间字段 acceptTime
- XNDJ 玄鸟大件：中文 statusName（已揽件、运输中、预约中、已送达、配送异常…），时间字段 scanDate
- SLLY 霜链冷运：数字 opCode（1、2、3、8、9）加温度 tempC，时间字段 acceptTime
"""
from __future__ import annotations

from typing import Literal

from xcsim.simkit.schema_base import CamelModel, LogisticsEnvelope

ERROR_CODES = {
    "B0001": "单号不存在",
    "B0002": "承运商不符",
    "B0003": "订单无包裹",
    "B0004": "重复报障（返回原单号）",
    "B0005": "报障单不存在",
    "B0009": "系统繁忙",
}

CARRIER_CODES = ("QLSY", "XNDJ", "SLLY")
IncidentKind = Literal["stalled", "disputed_delivery", "bad_address_return", "lost"]


class TraceItem(CamelModel):
    """一条轨迹。不同承运商只填各自用到的字段，其余为 null。"""

    op_code: int | None = None  # QLSY / SLLY
    status_name: str | None = None  # XNDJ
    op_desc: str
    op_site: str
    operator: str | None = None
    accept_time: str | None = None  # QLSY / SLLY
    scan_date: str | None = None  # XNDJ
    temp_c: float | None = None  # SLLY


class TraceData(CamelModel):
    mail_no: str
    carrier_code: str
    current_status: str
    traces: list[TraceItem]  # 默认时间倒序，可能有重复或乱序


class ParcelBrief(CamelModel):
    mail_no: str
    carrier_code: str
    current_status: str


class ParcelsData(CamelModel):
    order_no: str
    parcels: list[ParcelBrief]


class SignInfoData(CamelModel):
    mail_no: str
    sign_time: str | None = None
    sign_type: str | None = None  # hand / locker / station / proxy
    locker_in_time: str | None = None
    pickup_time: str | None = None
    signer: str | None = None


class IncidentCreate(CamelModel):
    mail_no: str
    kind: IncidentKind
    desc: str
    reporter: str


class IncidentData(CamelModel):
    incident_no: str
    mail_no: str
    kind: IncidentKind
    status: str
    conclusion: str | None = None
    opened_at: str


class CarrierInfo(CamelModel):
    carrier_code: str
    name: str
    usage: str
    status: str
    status_codes: dict[str, str]


class CarriersData(CamelModel):
    carriers: list[CarrierInfo]


TraceResponse = LogisticsEnvelope[TraceData]
ParcelsResponse = LogisticsEnvelope[ParcelsData]
SignInfoResponse = LogisticsEnvelope[SignInfoData]
IncidentResponse = LogisticsEnvelope[IncidentData]
CarriersResponse = LogisticsEnvelope[CarriersData]
