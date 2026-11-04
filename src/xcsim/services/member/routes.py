"""member 接口桩。WP6 会替换为真实实现。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from xcsim.simkit.auth import require
from xcsim.simkit.idempotency import idempotency_key
from xcsim.simkit.stub import mark_stub

from . import schemas as s

router = APIRouter(
    prefix="/member/v1",
    tags=["member"],
    dependencies=[Depends(mark_stub)],
    responses={401: {"model": s.UserResponse}, 403: {"model": s.UserResponse}},
)
READ = [Depends(require("member", "member:read"))]
WRITE = [Depends(require("member", "member:write"))]

_COUPON = s.CouponOut(
    coupon_id="CP-STUB-1", template_id="CS-COMP-10", status="unused", kind="compensation", threshold=0.0,
    amount=10.0, valid_from="2026-11-01T02:00:00Z", valid_to="2026-12-01T15:59:59Z",
)


@router.get("/users/lookup", response_model=s.UserLookupResponse, dependencies=READ, summary="按手机号或（尾号+姓名）查找用户，返回打码候选")
def lookup(
    phone: str | None = Query(None),
    phone_last4: str | None = Query(None, alias="phoneLast4"),
    name: str | None = Query(None),
) -> s.UserLookupResponse:
    cand = s.UserCandidate(user_id="U1048213", masked_name="陈*", masked_phone="100****8213", tier="normal")
    return s.UserLookupResponse(result=s.LookupResult(candidates=[cand]))


@router.get("/users/{user_id}", response_model=s.UserResponse, dependencies=READ, summary="用户基本信息（手机号打码）")
def get_user(user_id: str) -> s.UserResponse:
    return s.UserResponse(
        result=s.UserInfo(
            user_id=user_id, masked_name="陈*", masked_phone="100****8213", masked_email="c***@example.invalid",
            gender="female", birthday="1994-03-08", registered_at="2025-06-01T03:20:00Z", tier="normal", status="active",
        )
    )


@router.get("/users/{user_id}/addresses", response_model=s.AddressesResponse, dependencies=READ, summary="地址列表")
def addresses(user_id: str) -> s.AddressesResponse:
    a = s.AddressOut(
        address_id="A-STUB-1", receiver="陈*", masked_phone="100****8213", province="广东", city="广州",
        district="天河区", detail="示例路 1 号", is_remote=False, has_elevator=True, floor=6, is_default=True,
    )
    return s.AddressesResponse(result=s.AddressesResult(items=[a]))


@router.get("/users/{user_id}/tier", response_model=s.TierResponse, dependencies=READ, summary="会员等级、统计窗口、消费额、下次评定")
def tier(user_id: str) -> s.TierResponse:
    return s.TierResponse(
        result=s.TierInfo(
            user_id=user_id, tier="silver", assessed_at="2026-10-31T16:00:00Z", window_from="2025-10-31T16:00:00Z",
            window_to="2026-10-31T16:00:00Z", spend_yuan=3250.5, next_assess_at="2026-11-30T16:00:00Z",
            thresholds=s.TierThresholds(silver=2000.0, gold=8000.0),
        )
    )


@router.get("/users/{user_id}/points", response_model=s.PointsResponse, dependencies=READ, summary="积分：可用、待到账、即将过期")
def points(user_id: str) -> s.PointsResponse:
    return s.PointsResponse(
        result=s.PointsInfo(
            user_id=user_id, available=1280, pending=259,
            expiring_soon=[s.ExpiringPoints(points=100, expires_at="2026-12-31T15:59:59Z")],
        )
    )


@router.get("/users/{user_id}/points/ledger", response_model=s.LedgerResponse, dependencies=READ, summary="积分流水")
def ledger(user_id: str, page_no: int = Query(1, alias="pageNo", ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100)) -> s.LedgerResponse:
    item = s.LedgerItem(
        txn_id="PT-STUB-1", delta=259, kind="earn", order_id="XC2610184821", happened_at="2026-10-22T12:16:10Z",
        available_at="2026-11-01T06:10:00Z", expires_at="2027-12-31T15:59:59Z", balance_after=1539,
    )
    return s.LedgerResponse(result=s.LedgerPage(items=[item], page_no=page_no, page_size=page_size, total=1))


@router.get("/users/{user_id}/coupons", response_model=s.CouponsResponse, dependencies=READ, summary="优惠券列表")
def coupons(user_id: str, status: str | None = Query(None)) -> s.CouponsResponse:
    return s.CouponsResponse(result=s.CouponList(items=[_COUPON]))


@router.post(
    "/users/{user_id}/coupons/grant", status_code=201, response_model=s.GrantResponse, dependencies=WRITE,
    summary="发放补偿券（受客服权限与额度限制）",
)
def grant(user_id: str, body: s.GrantRequest, key: str = Depends(idempotency_key)) -> s.GrantResponse:
    return s.GrantResponse(result=_COUPON.model_copy(update={"template_id": body.template_id}))
