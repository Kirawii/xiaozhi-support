"""星川商城仿真业务接口（简化版，单进程）。

鉴权：业务接口用 `Authorization: Bearer <XC_AGENT_TOKEN>`；控制接口用 `X-Control-Token: <XC_CONTROL_TOKEN>`。
响应：业务接口 `{"code": 0, "message": "ok", "data": ...}`；错误 `{"code": "错误码", "message": "...", "detail": {...}}`。
物流接口模拟第三方快递公司，风格不同：`{"success": "true", "errCode": "", "errMsg": "", "data": ...}`，时间为北京时间字符串。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time as walltime
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from xcsim.policy import (
    advance_refund_decision, arrival_eta, can_apply_again, exchange_block_rule, fresh_refund, in_service_hours,
    is_remote_province, manual_review_due_at, needs_inspection_report, needs_manual_review, price_protection,
    quality_options, remaining_applications, return_block_rule, return_window, within_fresh_claim,
    within_wrong_or_missing,
)
from xcsim.policy import is_return_window_open
from xcsim.policy.loader import get_policy

from . import world as W

AGENT_TOKEN = os.environ.get("XC_AGENT_TOKEN", "agent-dev-token")
CONTROL_TOKEN = os.environ.get("XC_CONTROL_TOKEN", "control-dev-token")


def auto_settle_seconds() -> float:
    return float(os.environ.get("XC_AUTO_SETTLE_SECONDS", "5"))


app = FastAPI(title="星川商城仿真业务接口", version="lite-1.0",
              description="虚构电商“星川商城”的业务中台接口，供客服 Agent 练习对接。所有数据均为虚构。")
STATE: dict = {"faults": [], "idem": {}}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        self.status, self.code, self.message, self.detail = status, code, message, detail or {}


@app.exception_handler(ApiError)
async def _api_error(_: Request, e: ApiError):
    return JSONResponse({"code": e.code, "message": e.message, "detail": e.detail}, status_code=e.status)


def ok(data, status: int = 200, headers: dict | None = None):
    return JSONResponse({"code": 0, "message": "ok", "data": data}, status_code=status, headers=headers)


def w() -> W.World:
    return W.get_world()


def now() -> datetime:
    return w().clock.now


def require_agent(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "缺少或错误的 Authorization 头")
    if authorization.removeprefix("Bearer ").strip() != AGENT_TOKEN:
        raise ApiError(403, "FORBIDDEN", "令牌无效")


def require_control(x_control_token: str | None = Header(default=None)):
    if x_control_token != CONTROL_TOKEN:
        raise ApiError(403, "FORBIDDEN", "需要控制令牌 X-Control-Token")


# ---------------------------------------------------------------- 故障注入
@app.middleware("http")
async def fault_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/control"):
        for rule in STATE["faults"]:
            if request.method != rule.get("method", request.method) or not path.startswith(rule["path"]):
                continue
            if rule.get("times", 0) and rule.get("_hits", 0) >= rule["times"]:
                continue
            rule["_hits"] = rule.get("_hits", 0) + 1
            kind = rule["kind"]
            if kind == "latency":
                await asyncio.sleep(rule.get("latency_ms", 1000) / 1000)
                break
            if kind == "http_429":
                return JSONResponse({"code": "RATE_LIMITED", "message": "请求过于频繁"}, status_code=429,
                                    headers={"Retry-After": str(rule.get("retry_after", 2))})
            if kind == "http_503":
                return JSONResponse({"code": "SERVICE_UNAVAILABLE", "message": "服务暂时不可用"}, status_code=503)
            if kind == "timeout":
                await asyncio.sleep(rule.get("hold_ms", 30000) / 1000)
                return JSONResponse({"code": "GATEWAY_TIMEOUT", "message": "上游超时"}, status_code=504)
            if kind == "timeout_after_commit":
                await call_next(request)  # 请求已经执行并生效
                await asyncio.sleep(rule.get("hold_ms", 30000) / 1000)
                return JSONResponse({"code": "GATEWAY_TIMEOUT", "message": "上游超时"}, status_code=504)
    response = await call_next(request)
    response.headers["X-Sim-Now"] = W.iso(now())
    return response


# ---------------------------------------------------------------- 视图
def _mask(phone: str) -> str:
    return phone[:3] + "****" + phone[-4:]


def user_view(u: dict) -> dict:
    return {"user_id": u["user_id"], "name": u["name"], "phone": _mask(u["phone"]), "tier": u["tier"],
            "points": u["points"], "registered_at": W.iso(u["registered_at"]),
            "address": u["address"] | {"phone": _mask(u["address"]["phone"])}}


def get_user(uid: str) -> dict:
    u = w().users.get(uid)
    if not u:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    return u


def get_order(oid: str, uid: str) -> dict:
    o = w().orders.get(oid)
    if not o or o["user_id"] != uid or o["created_at"] > now():
        raise ApiError(404, "ORDER_NOT_FOUND", "订单不存在")
    return o


def order_view(o: dict, brief: bool = False) -> dict:
    wd = w()
    v = {"order_id": o["order_id"], "status": wd.order_status(o), "created_at": W.iso(o["created_at"]),
         "paid_at": W.iso(o["paid_at"]) if o["paid_at"] and o["paid_at"] <= now() else None,
         "total_paid": W.yuan(o["total_paid"]), "item_count": len(o["items"])}
    if brief:
        v["items"] = [ln["name"] for ln in o["items"]]
        return v
    parcels = []
    for m in o["parcel_ids"]:
        p = wd.parcels[m]
        st = wd.parcel_state(p)
        if st["status"] == "created":
            continue
        parcels.append({"mail_no": m, "carrier_code": p["carrier_code"], "carrier_name": p["carrier_name"],
                        "status": st["status"], "item_ids": p["item_ids"]})
    v |= {
        "kind": o["kind"], "cancelled_at": W.iso(o["cancelled_at"]) if o["cancelled_at"] and o["cancelled_at"] <= now() else None,
        "items": [{"item_id": ln["item_id"], "sku_id": ln["sku_id"], "name": ln["name"], "qty": ln["qty"],
                   "unit_price": W.yuan(ln["unit_price"]), "discount": W.yuan(ln["discount_alloc"]),
                   "paid_amount": W.yuan(ln["paid_amount"]), "is_gift": ln["is_gift"],
                   "gift_value": W.yuan(ln["gift_value"]) if ln["is_gift"] else None,
                   "seal_status": ln["seal_status"], "activation_status": ln["activation_status"]} for ln in o["items"]],
        "goods_total": W.yuan(o["goods_total"]), "coupon_discount": W.yuan(o["coupon_discount"]),
        "goods_paid": W.yuan(o["goods_paid"]), "points_deduction": W.yuan(o["points_deduction"]),
        "shipping_fee": W.yuan(o["shipping_fee"]), "bulky_fee": W.yuan(o["bulky_fee"]),
        "cold_chain_fee": W.yuan(o["cold_chain_fee"]), "floor_fee": W.yuan(o["floor_fee"]),
        "shipping_rule_version": o["shipping_rule_version"], "payment_channel": o["payment_channel"],
        "payment_channel_name": getattr(get_policy().payment_channels, o["payment_channel"], None) if o["payment_channel"] else None,
        "installments": o["installments"], "tier_at_order": o["tier_at_order"],
        "address": o["address"] | {"phone": _mask(o["address"]["phone"])},
        "address_change_count": o["address_change_count"], "parcels": parcels,
    }
    return v


# ---------------------------------------------------------------- 时间、用户、商品
@app.get("/api/v1/time", dependencies=[Depends(require_agent)], tags=["通用"], summary="仿真当前时间（Agent 必须以此为“今天”）")
def get_time():
    n = now()
    return ok({"now": W.iso(n), "weekday": n.isoweekday(), "in_human_service_hours": in_service_hours(n)})


@app.get("/api/v1/users/lookup", dependencies=[Depends(require_agent)], tags=["会员"], summary="按手机号，或手机尾号+姓名查找用户")
def lookup_user(phone: str | None = None, phone_last4: str | None = None, name: str | None = None):
    if phone:
        hits = [u for u in w().users.values() if u["phone"] == phone]
    elif phone_last4 and name:
        hits = [u for u in w().users.values() if u["phone"].endswith(phone_last4) and u["name"] == name]
    else:
        raise ApiError(400, "INVALID_PARAMS", "需要 phone，或同时提供 phone_last4 和 name")
    return ok({"candidates": [{"user_id": u["user_id"], "name": u["name"], "phone": _mask(u["phone"])} for u in hits]})


@app.get("/api/v1/users/{user_id}", dependencies=[Depends(require_agent)], tags=["会员"], summary="用户信息、会员等级、积分")
def user_detail(user_id: str):
    return ok(user_view(get_user(user_id)))


@app.get("/api/v1/products", dependencies=[Depends(require_agent)], tags=["商品"], summary="搜索商品")
def list_products(q: str | None = None):
    wd = w()
    rows = [s for s in wd.spus.values() if not q or q.lower() in s["name"].lower()]
    return ok({"items": [{"spu_id": s["spu_id"], "name": s["name"], "category": s["category"]} for s in rows]})


@app.get("/api/v1/products/{spu_id}", dependencies=[Depends(require_agent)], tags=["商品"], summary="商品详情（含 SKU 现价和详情页售后说明）")
def product_detail(spu_id: str):
    wd = w()
    s = wd.spus.get(spu_id)
    if not s:
        raise ApiError(404, "PRODUCT_NOT_FOUND", "商品不存在")
    skus = [{"sku_id": k["sku_id"], "spec": k["spec"], "price": W.yuan(k["current_price"]),
             "list_price": W.yuan(k["list_price"]), "price_type": k["price_type"], "in_stock": k["stock"] > 0}
            for k in (wd.skus[i] for i in s["sku_ids"])]
    return ok({"spu_id": s["spu_id"], "name": s["name"], "category": s["category"], "is_bulky": s["line"] == "bulky",
               "is_fresh": s["line"] == "fresh", "refurbished": s["refurbished"],
               "detail_aftersale_notice": s["detail_aftersale_override"], "kb_doc_id": s["kb_doc_id"], "skus": skus})


# ---------------------------------------------------------------- 订单
@app.get("/api/v1/orders", dependencies=[Depends(require_agent)], tags=["订单"], summary="用户的订单列表（新到旧）")
def list_orders(user_id: str, status: str | None = None, page: int = 1, page_size: int = 10):
    get_user(user_id)
    rows = [o for o in w().orders.values() if o["user_id"] == user_id and o["created_at"] <= now()]
    rows.sort(key=lambda o: o["created_at"], reverse=True)
    views = [order_view(o, brief=True) for o in rows]
    if status:
        views = [v for v in views if v["status"] == status]
    page_size = max(1, min(page_size, 50))
    return ok({"total": len(views), "page": page, "items": views[(page - 1) * page_size: page * page_size]})


@app.get("/api/v1/orders/{order_id}", dependencies=[Depends(require_agent)], tags=["订单"], summary="订单详情（明细、优惠分摊、费用、包裹）")
def order_detail(order_id: str, user_id: str):
    return ok(order_view(get_order(order_id, user_id)))


def _create_refund(o: dict, case: dict | None, amount: int, *, advance: bool = False, key: str | None = None) -> dict:
    wd = w()
    rid = "RF" + wd.next_id("R", 10)[1:]
    review = needs_manual_review(amount)
    ref = {"refund_id": rid, "order_id": o["order_id"], "case_id": case["case_id"] if case else None, "amount": amount,
           "status": "reviewing" if review else "processing", "channel": o["payment_channel"], "created_at": now(),
           "created_wall": walltime.monotonic(), "due_at": now() + timedelta(hours=1),
           "review_due_at": manual_review_due_at(now()) if review else None, "failure_code": None,
           "forced_result": None, "idempotency_key": key, "advance": advance, "settled_at": None}
    wd.refunds[rid] = ref
    return ref


@app.post("/api/v1/orders/{order_id}/cancel", dependencies=[Depends(require_agent)], tags=["订单"], summary="取消订单（未发货）")
def cancel_order(order_id: str, body: dict, idempotency_key: str | None = Header(default=None)):
    return idempotent(f"cancel:{order_id}", idempotency_key, body, lambda: _cancel_order(order_id, body))


def _cancel_order(order_id: str, body: dict):
    o = get_order(order_id, body.get("user_id", ""))
    st = w().order_status(o)
    if st == "cancelled":
        raise ApiError(409, "ORDER_ALREADY_CANCELLED", "订单已取消")
    if st not in ("pending_payment", "paid"):
        raise ApiError(409, "ORDER_ALREADY_SHIPPED", "订单已发货，不能取消；可在派送时拒收", {"status": st})
    o["cancelled_at"] = now()
    for m in o["parcel_ids"]:  # 未揽收的包裹作废
        w().parcels[m]["traces"] = [t for t in w().parcels[m]["traces"] if t["time"] <= now()]
    refund = None
    if st == "paid":
        refund = _create_refund(o, None, o["total_paid"])
        refund["due_at"] = now()  # 未发货取消：即时退款
    return ok({"order_id": order_id, "status": "cancelled", "refund": refund_view(refund) if refund else None})


@app.post("/api/v1/orders/{order_id}/address", dependencies=[Depends(require_agent)], tags=["订单"], summary="修改收货地址（未发货，限一次）")
def change_address(order_id: str, body: dict, idempotency_key: str | None = Header(default=None)):
    return idempotent(f"address:{order_id}", idempotency_key, body, lambda: _change_address(order_id, body))


def _change_address(order_id: str, body: dict):
    o = get_order(order_id, body.get("user_id", ""))
    st = w().order_status(o)
    if st not in ("pending_payment", "paid"):
        raise ApiError(409, "ORDER_ALREADY_SHIPPED", "订单已发货，不能修改地址，可联系快递转寄（费用自理）")
    if o["address_change_count"] >= get_policy().order.address_change_max_times:
        raise ApiError(409, "ADDRESS_CHANGE_LIMIT", "每个订单只能修改一次地址")
    prov = body.get("province", "")
    has_fresh = any(w().spus[ln["spu_id"]]["line"] == "fresh" for ln in o["items"] if not ln["is_gift"])
    if has_fresh and is_remote_province(prov):
        raise ApiError(422, "FRESH_NOT_DELIVERABLE", "生鲜商品不配送新疆、西藏、青海")
    old_total = o["shipping_fee"] + o["bulky_fee"] + o["cold_chain_fee"] + o["floor_fee"]
    o["address"] = o["address"] | {"province": prov, "city": body.get("city", ""), "detail": body.get("detail", "")}
    fee = w()._shipping(o["items"], o["address"], o["paid_at"] or o["created_at"], o["tier_at_order"])
    o.update(shipping_fee=fee.shipping_fee_fen, bulky_fee=fee.bulky_fee_fen, cold_chain_fee=fee.cold_chain_fee_fen,
             floor_fee=fee.floor_fee_fen)
    o["address_change_count"] += 1
    return ok({"order_id": order_id, "address": o["address"], "fee_delta": W.yuan(fee.total_fen - old_total)})


# ---------------------------------------------------------------- 物流（第三方风格）
_STATUS_CN = {"collected": "已揽收", "in_transit": "运输中", "out_for_delivery": "派送中", "in_locker": "待取件",
              "delivered": "已签收", "exception": "异常", "returning": "退回中", "returned": "已退回", "created": "待揽收"}


@app.get("/api/v1/logistics/{mail_no}", dependencies=[Depends(require_agent)], tags=["物流"],
         summary="物流轨迹（第三方风格：HTTP 恒为 200，看 success 字段；时间为北京时间字符串）")
def trace(mail_no: str):
    wd = w()
    p = wd.parcels.get(mail_no)
    vis = wd.visible_traces(p) if p else []
    if not p or not vis:
        return JSONResponse({"success": "false", "errCode": "B0001", "errMsg": "运单号不存在或暂无轨迹", "data": None})
    st = wd.parcel_state(p)
    traces = sorted(vis, key=lambda t: t["time"], reverse=True)
    sign_desc = next((t["desc"] for t in vis if t["code"] == "40"), "")
    return JSONResponse({"success": "true", "errCode": "", "errMsg": "", "data": {
        "mailNo": mail_no, "carrierCode": p["carrier_code"], "carrierName": p["carrier_name"],
        "currentStatus": _STATUS_CN[st["status"]], "lastUpdateTime": W.bjstr(traces[0]["time"]),
        "signTime": W.bjstr(st["signed_at"]), "lockerInTime": W.bjstr(st["locker_in_at"]),
        "signer": sign_desc.split("签收人：")[-1] if sign_desc else None,
        "traces": [{"acceptTime": W.bjstr(t["time"]), "opCode": t["code"], "opDesc": t["desc"]} for t in traces],
    }})


# ---------------------------------------------------------------- 售后（写接口强校验规则）
def _case_view(c: dict) -> dict:
    return {k: (W.iso(v) if isinstance(v, datetime) else v) for k, v in c.items()} | {
        "refund_preview": W.yuan(c["refund_amount"])}


def _reject(code: str, message: str, **detail):
    raise ApiError(422, code, message, detail)


def idempotent(scope: str, key: str | None, body: dict, fn):
    """写接口的通用幂等：同键同参返回第一次的结果；同键异参返回 422。不带键时照常执行。"""
    if not key:
        return fn()
    fp = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    saved = STATE["idem"].get((scope, key))
    if saved:
        if saved["fp"] != fp:
            raise ApiError(422, "IDEMPOTENCY_KEY_REUSED", "同一个幂等键不能用于不同的请求")
        return JSONResponse(saved["body"], status_code=saved["status"], headers={"Idempotent-Replayed": "true"})
    resp = fn()
    if resp.status_code < 300:
        STATE["idem"][(scope, key)] = {"fp": fp, "body": json.loads(resp.body), "status": resp.status_code}
    return resp


@app.post("/api/v1/identity/verify", dependencies=[Depends(require_agent)], tags=["会员"],
          summary="身份核验：订单号 + 收货手机号后 4 位（与教程 verify_identity 一致）")
def verify_identity(body: dict):
    o = w().orders.get(str(body.get("order_id", "")))
    last4 = str(body.get("phone_last4", ""))
    if not o or o["created_at"] > now() or len(last4) != 4:
        return ok({"verified": False, "user_id": None})
    u = w().users[o["user_id"]]
    hit = last4 in (u["phone"][-4:], o["address"]["phone"][-4:])
    return ok({"verified": hit, "user_id": u["user_id"] if hit else None, "name": u["name"] if hit else None})


@app.post("/api/v1/aftersales", dependencies=[Depends(require_agent)], tags=["售后"], status_code=201,
          summary="提交售后申请（可带 Idempotency-Key；body 里 dry_run=true 时只校验和试算、不创建，用于两阶段确认）")
def create_aftersale(body: dict, idempotency_key: str | None = Header(default=None)):
    if body.get("dry_run"):
        return _create_aftersale(body, dry_run=True)
    return idempotent("aftersale", idempotency_key, body, lambda: _create_aftersale(body, dry_run=False))


def _create_aftersale(body: dict, dry_run: bool):
    wd = w()
    uid, ctype = body.get("user_id", ""), body.get("type")
    o = get_order(body.get("order_id", ""), uid)
    item = next((ln for ln in o["items"] if ln["item_id"] == body.get("item_id")), None)
    if not item or item["is_gift"]:
        raise ApiError(404, "ITEM_NOT_FOUND", "订单中没有这个商品（赠品随主商品处理）")
    if ctype not in ("return_7day", "size_exchange", "quality_refund", "quality_exchange", "quality_repair",
                     "wrong_missing", "fresh_claim"):
        raise ApiError(400, "INVALID_TYPE", "不支持的售后类型")
    signed = wd.item_signed_at(o, item["item_id"])
    if not signed:
        _reject("AS_NOT_DELIVERED", "商品尚未签收；未发货可取消订单，派送中可拒收")
    spu, sku = wd.spus[item["spu_id"]], wd.skus[item["sku_id"]]
    n = now()
    prior = [c for c in wd.cases.values() if c["item_id"] == item["item_id"]]
    case = {"type": ctype, "needs_inspection_report": False, "needs_inspection": False, "manual_review_required": False,
            "refund_amount": 0, "deadline": None, "remaining_applications": None, "gift_must_return": None,
            "status": "awaiting_return", "converted_from": None}

    if ctype in ("return_7day", "size_exchange"):
        flag = item["seal_status"] == "opened"
        block = return_block_rule(tags=spu["tags"], kind=spu["kind"], opened=flag, seal_removed=flag,
                                  activated=item["activation_status"] == "activated")
        if block:
            _reject(block, "该商品不支持七天无理由退货", category=spu["category"])
        if spu["detail_aftersale_override"]:
            _reject("AS_DETAIL_OVERRIDE", "商品详情页另有售后说明，以详情页为准", notice=spu["detail_aftersale_override"])
        win = return_window(signed)
        if not is_return_window_open(win, n):
            _reject("AS_WINDOW_EXPIRED", "已超过无理由退换期限", deadline=W.iso(win.deadline_at),
                    rule_version=win.version, signed_at=W.iso(signed))
        case["deadline"] = W.iso(win.deadline_at)
        if ctype == "return_7day":
            used = sum(1 for c in prior if c["type"] == "return_7day")
            if not can_apply_again(used):
                _reject("AS_APPLY_LIMIT", "同一商品无理由退货最多申请 2 次（含已撤销）", used=used)
            case["remaining_applications"] = remaining_applications(used + 1)
            case["refund_amount"] = item["paid_amount"]
            gifts = [g for g in o["items"] if g["is_gift"]]
            if gifts:
                case["gift_must_return"] = [{"name": g["name"], "gift_value": W.yuan(g["gift_value"])} for g in gifts]
        else:
            done = sum(1 for c in prior if c["type"] == "size_exchange" and c["status"] != "withdrawn")
            block = exchange_block_rule(spu["exchange_category"] or "", done)
            if block:
                _reject(block, "该商品不支持换码，或已换过一次")
            target = wd.skus.get(body.get("target_sku_id", ""))
            if not target or target["spu_id"] != spu["spu_id"] or target["stock"] <= 0:
                _reject("AS_EXCHANGE_OUT_OF_STOCK", "目标规格不存在或无货，可退货后重新下单")
            case["target_sku_id"] = target["sku_id"]

    elif ctype in ("quality_refund", "quality_exchange", "quality_repair", "wrong_missing"):
        if ctype == "wrong_missing":
            if within_wrong_or_missing(signed, n):
                case["status"] = "approved"
            else:  # 超过 48 小时，按一般质量问题处理并检测
                ctype = case["type"] = "quality_refund"
                case["converted_from"], case["needs_inspection"] = "wrong_missing", True
        if ctype != "wrong_missing":
            opts = quality_options(signed, n, exchange_out_of_stock=sku["stock"] <= 0)
            want = {"quality_refund": "refund", "quality_exchange": "exchange", "quality_repair": "repair"}[ctype]
            if want not in opts:
                _reject("AS_OPTION_NOT_AVAILABLE", "当前时段不支持该处理方式", allowed_options=list(opts),
                        signed_at=W.iso(signed))
        case["needs_inspection_report"] = needs_inspection_report(
            kind=spu["kind"], unit_price_fen=item["unit_price"], is_digital_category=spu["category"] == "数码")
        if ctype in ("quality_refund", "wrong_missing"):
            case["refund_amount"] = item["paid_amount"]

    else:  # fresh_claim
        if "fresh" not in spu["tags"]:
            _reject("AS_NOT_FRESH", "该商品不是生鲜，请选择其他售后类型")
        if not within_fresh_claim(signed, n):
            _reject("AS_FRESH_CLAIM_EXPIRED", "生鲜问题需在签收后 24 小时内反馈", signed_at=W.iso(signed))
        try:
            ratio = Decimal(str(body.get("bad_ratio")))
            assert 0 < ratio <= 1
        except (InvalidOperation, AssertionError):
            raise ApiError(400, "INVALID_PARAMS", "fresh_claim 需要 bad_ratio（0–1 之间，如 0.2）")
        fr = fresh_refund(item["paid_amount"], o["cold_chain_fee"], ratio)
        case |= {"refund_amount": fr.refund_fen, "full_refund": fr.full_refund,
                 "cold_chain_refunded": fr.cold_chain_refunded, "status": "approved"}

    case["manual_review_required"] = needs_manual_review(case["refund_amount"])
    if dry_run:  # 预检：规则全部通过，返回试算结果，不写入任何数据
        preview = case | {"case_id": None, "order_id": o["order_id"], "item_id": item["item_id"], "user_id": uid,
                          "created_at": None, "dry_run": True}
        return ok(_case_view(preview))
    cid = "AS" + wd.next_id("C", 8)[1:]
    case |= {"case_id": cid, "user_id": uid, "order_id": o["order_id"], "item_id": item["item_id"],
             "reason": body.get("reason", ""), "created_at": n, "withdrawn_at": None, "return_mail_no": None}
    wd.cases[cid] = case
    return ok(_case_view(case), status=201)


@app.get("/api/v1/aftersales", dependencies=[Depends(require_agent)], tags=["售后"], summary="售后单列表")
def list_cases(user_id: str, order_id: str | None = None):
    rows = [c for c in w().cases.values() if c["user_id"] == user_id and c["created_at"] <= now()
            and (not order_id or c["order_id"] == order_id)]
    return ok({"items": [_case_view(c) for c in rows]})


def _get_case(case_id: str, uid: str) -> dict:
    c = w().cases.get(case_id)
    if not c or c["user_id"] != uid:
        raise ApiError(404, "CASE_NOT_FOUND", "售后单不存在")
    return c


@app.post("/api/v1/aftersales/{case_id}/withdraw", dependencies=[Depends(require_agent)], tags=["售后"], summary="撤销售后申请（仍计入申请次数）")
def withdraw_case(case_id: str, body: dict):
    c = _get_case(case_id, body.get("user_id", ""))
    if c["status"] not in ("submitted", "approved", "awaiting_return"):
        raise ApiError(409, "ILLEGAL_TRANSITION", "当前状态不能撤销", {"status": c["status"]})
    c["status"], c["withdrawn_at"] = "withdrawn", now()
    return ok(_case_view(c))


@app.post("/api/v1/aftersales/{case_id}/return-shipment", dependencies=[Depends(require_agent)], tags=["售后"],
          summary="登记退货寄回单号（金星会员满足条件时自动先行退款）")
def return_shipment(case_id: str, body: dict):
    wd = w()
    c = _get_case(case_id, body.get("user_id", ""))
    if c["status"] != "awaiting_return":
        raise ApiError(409, "ILLEGAL_TRANSITION", "当前状态不能登记寄回", {"status": c["status"]})
    mail = str(body.get("mail_no", ""))
    if len(mail) < 10 or body.get("carrier_code") not in ("QLSY", "XNDJ", "SLLY"):
        raise ApiError(422, "INVALID_TRACKING_NO", "物流单号或承运商无效")
    c["status"], c["return_mail_no"] = "return_shipped", mail
    user = wd.users[c["user_id"]]
    adv = None
    if c["type"] == "return_7day":
        d = advance_refund_decision(tier=user["tier"], gross_refund_fen=c["refund_amount"], has_valid_return_tracking=True,
                                    suspended_until=user["advance_refund_suspended_until"], now=now())
        if d.allowed:
            adv = refund_view(_create_refund(wd.orders[c["order_id"]], c, c["refund_amount"], advance=True))
    return ok(_case_view(c) | {"advance_refund": adv})


# ---------------------------------------------------------------- 退款（异步、幂等）
def _settle(ref: dict) -> None:
    n = now()
    if ref["status"] == "reviewing" and ref["review_due_at"] and n >= ref["review_due_at"]:
        ref["status"], ref["due_at"], ref["created_wall"] = "processing", ref["review_due_at"] + timedelta(hours=1), None
    if ref["status"] == "processing":
        wall_ok = (ref["created_wall"] is not None and auto_settle_seconds() > 0
                   and walltime.monotonic() - ref["created_wall"] >= auto_settle_seconds())
        if wall_ok or n >= ref["due_at"]:
            ref["status"] = ref["forced_result"] or "succeeded"
            ref["settled_at"] = n
            if ref["status"] == "failed":
                ref["failure_code"] = ref["failure_code"] or "CHANNEL_ERROR"


def refund_view(ref: dict) -> dict:
    _settle(ref)
    v = {"refund_id": ref["refund_id"], "order_id": ref["order_id"], "case_id": ref["case_id"],
         "amount": W.yuan(ref["amount"]), "status": ref["status"], "channel": ref["channel"],
         "created_at": W.iso(ref["created_at"]), "settled_at": W.iso(ref["settled_at"]),
         "failure_code": ref["failure_code"], "advance_refund": ref["advance"],
         "manual_review_due_at": W.iso(ref["review_due_at"])}
    if ref["status"] == "succeeded" and ref["channel"]:
        lo, hi = arrival_eta(ref["channel"], ref["settled_at"] or ref["created_at"])
        v["arrival_eta"] = {"earliest": W.iso(lo), "latest": W.iso(hi)}
    return v


@app.post("/api/v1/refunds", dependencies=[Depends(require_agent)], tags=["退款"], status_code=202,
          summary="发起退款（必须带 Idempotency-Key；异步，先返回 processing 或 reviewing）")
def create_refund(body: dict, idempotency_key: str | None = Header(default=None)):
    if not idempotency_key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "退款请求必须带 Idempotency-Key 头")
    fp = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    saved = STATE["idem"].get(idempotency_key)
    if saved:
        if saved["fp"] != fp:
            raise ApiError(422, "IDEMPOTENCY_KEY_REUSED", "同一个幂等键不能用于不同的请求")
        return ok(refund_view(w().refunds[saved["refund_id"]]), status=202, headers={"Idempotent-Replayed": "true"})
    wd = w()
    o = wd.orders.get(body.get("order_id", ""))
    if not o or not o["paid_at"]:
        raise ApiError(404, "ORDER_NOT_FOUND", "订单不存在或未付款")
    try:
        amount = int((Decimal(str(body.get("amount"))) * 100).to_integral_exact())
        assert amount > 0
    except (InvalidOperation, AssertionError, ArithmeticError):
        raise ApiError(400, "INVALID_AMOUNT", "amount 必须是正数，单位元，最多两位小数")
    case = None
    if body.get("case_id"):
        case = wd.cases.get(body["case_id"])
        if not case or case["order_id"] != o["order_id"]:
            raise ApiError(404, "CASE_NOT_FOUND", "售后单不存在")
        if case["status"] not in ("inspect_passed", "approved", "refund_pending"):
            raise ApiError(422, "CASE_NOT_APPROVED", "售后单尚未审核或验收通过", {"case_status": case["status"]})
    for r in wd.refunds.values():
        same = r["case_id"] == (case["case_id"] if case else None) and r["order_id"] == o["order_id"]
        if same:
            _settle(r)
            if r["status"] in ("reviewing", "processing"):
                raise ApiError(409, "REFUND_ALREADY_IN_PROGRESS", "已有进行中的退款", {"refund_id": r["refund_id"]})
            if r["status"] == "succeeded":
                raise ApiError(409, "REFUND_ALREADY_SUCCEEDED", "该笔已退款成功", {"refund_id": r["refund_id"]})
    refunded = sum(r["amount"] for r in wd.refunds.values() if r["order_id"] == o["order_id"] and r["status"] != "failed")
    if amount > o["total_paid"] - refunded:
        raise ApiError(422, "REFUND_EXCEEDS_REFUNDABLE", "退款金额超过可退金额",
                       {"refundable": W.yuan(o["total_paid"] - refunded)})
    ref = _create_refund(o, case, amount, key=idempotency_key)
    STATE["idem"][idempotency_key] = {"fp": fp, "refund_id": ref["refund_id"]}
    return ok(refund_view(ref), status=202)


@app.get("/api/v1/refunds", dependencies=[Depends(require_agent)], tags=["退款"], summary="按订单或幂等键查询退款")
def list_refunds(order_id: str | None = None, idempotency_key: str | None = None):
    if not order_id and not idempotency_key:
        raise ApiError(400, "INVALID_PARAMS", "需要 order_id 或 idempotency_key")
    rows = [r for r in w().refunds.values() if r["created_at"] <= now()
            and (not order_id or r["order_id"] == order_id)
            and (not idempotency_key or r["idempotency_key"] == idempotency_key)]
    return ok({"items": [refund_view(r) for r in rows]})


@app.get("/api/v1/refunds/{refund_id}", dependencies=[Depends(require_agent)], tags=["退款"], summary="退款详情")
def refund_detail(refund_id: str):
    r = w().refunds.get(refund_id)
    if not r:
        raise ApiError(404, "REFUND_NOT_FOUND", "退款单不存在")
    return ok(refund_view(r))


# ---------------------------------------------------------------- 价保
_PP_CODES = {"window": ("PP_WINDOW_EXPIRED", "已超过价格保护期"), "price_type": ("PP_PRICE_TYPE_EXCLUDED", "降价为秒杀、拼团、直播或清仓价，不适用价保"),
             "order_kind": ("PP_ORDER_KIND_EXCLUDED", "预售商品不适用价保"), "gift_item": ("PP_GIFT_EXCLUDED", "赠品不适用价保"),
             "returned": ("PP_RETURN_APPLIED", "已申请退货的商品不适用价保"), "no_diff": ("PP_NO_DIFF", "实付单价不高于现价，无差价可补")}


@app.post("/api/v1/price-protections", dependencies=[Depends(require_agent)], tags=["价保"], summary="申请价格保护（补差价到星川余额）")
def apply_price_protection(body: dict):
    wd = w()
    uid = body.get("user_id", "")
    o, user = get_order(body.get("order_id", ""), uid), get_user(uid)
    item = next((ln for ln in o["items"] if ln["item_id"] == body.get("item_id")), None)
    if not item:
        raise ApiError(404, "ITEM_NOT_FOUND", "订单中没有这个商品")
    if item["item_id"] in wd.price_claims:
        raise ApiError(409, "PP_ALREADY_CLAIMED", "每个订单中的每种商品只能申请一次价保")
    sku = wd.skus.get(item["sku_id"])
    returned = any(c["item_id"] == item["item_id"] and c["status"] != "withdrawn" and
                   c["type"] in ("return_7day", "quality_refund") for c in wd.cases.values())
    r = price_protection(tier=user["tier"], paid_at=o["paid_at"], now=now(),
                         paid_unit_fen=item["paid_amount"] // max(item["qty"], 1),
                         current_price_fen=sku["current_price"] if sku else 0,
                         current_price_type=sku["price_type"] if sku else "regular", order_kind=o["kind"],
                         qty=item["qty"], is_gift=item["is_gift"], has_return_application=returned)
    if not r.eligible:
        code, msg = _PP_CODES[r.reason]
        raise ApiError(422, code, msg, {"day": r.day_k, "window_days": r.window_days})
    wd.price_claims[item["item_id"]] = {"diff": r.diff_fen, "at": now()}
    return ok({"order_id": o["order_id"], "item_id": item["item_id"], "diff": W.yuan(r.diff_fen),
               "payout_to": "星川余额", "day": r.day_k, "window_days": r.window_days})


# ---------------------------------------------------------------- 工单（转人工）
@app.post("/api/v1/tickets", dependencies=[Depends(require_agent)], tags=["工单"], status_code=201, summary="创建转人工工单")
def create_ticket(body: dict):
    wd = w()
    user = get_user(body.get("user_id", ""))
    if not body.get("summary"):
        raise ApiError(400, "SUMMARY_REQUIRED", "需要 summary：给人工坐席的问题摘要")
    n = now()
    on = in_service_hours(n)
    queue = sum(1 for t in wd.tickets.values() if t["status"] == "queued") + 1
    wait = getattr(get_policy().cs_authority.ticket_first_response_minutes, user["tier"], 30)
    tid = "TK" + wd.next_id("T", 8)[1:]
    nxt = None
    if not on:
        open_h, open_m = (int(x) for x in get_policy().service_hours.human.open.split(":"))
        d = n.date() if n.hour < open_h else (n + timedelta(days=1)).date()
        nxt = datetime(d.year, d.month, d.day, open_h, open_m, tzinfo=W.BJ)
    t = {"ticket_id": tid, "user_id": user["user_id"], "order_id": body.get("order_id"), "category": body.get("category", "other"),
         "summary": body["summary"], "status": "queued", "created_at": n, "in_service_hours": on,
         "queue_position": queue, "estimated_wait_minutes": wait if on else None, "next_service_time": W.iso(nxt),
         "messages": []}
    wd.tickets[tid] = t
    return ok({k: (W.iso(v) if isinstance(v, datetime) else v) for k, v in t.items()}, status=201)


@app.get("/api/v1/tickets/{ticket_id}", dependencies=[Depends(require_agent)], tags=["工单"], summary="工单详情")
def ticket_detail(ticket_id: str):
    t = w().tickets.get(ticket_id)
    if not t:
        raise ApiError(404, "TICKET_NOT_FOUND", "工单不存在")
    return ok({k: (W.iso(v) if isinstance(v, datetime) else v) for k, v in t.items()})


# ---------------------------------------------------------------- 控制接口（仅测试/评测使用）
def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=W.BJ)


@app.post("/control/reset", dependencies=[Depends(require_control)], tags=["控制"], summary="重置全部数据、时钟和故障")
def control_reset(body: dict | None = None):
    body = body or {}
    anchor = _parse_dt(body["anchor"]) if body.get("anchor") else W.DEFAULT_ANCHOR
    wd = W.reset_world(seed=int(body.get("seed", 42)), anchor=anchor)
    STATE["faults"], STATE["idem"] = [], {}
    return ok({"seed": wd.seed, "anchor": W.iso(wd.anchor), "now": W.iso(wd.clock.now), "fingerprint": wd.fingerprint()})


@app.get("/control/clock", dependencies=[Depends(require_control)], tags=["控制"], summary="读取仿真时钟")
def control_clock_get():
    return ok({"now": W.iso(now())})


@app.post("/control/clock", dependencies=[Depends(require_control)], tags=["控制"], summary="设置时钟：{set: ISO 时间} 或 {advance_minutes: N}")
def control_clock_set(body: dict):
    c = w().clock
    if body.get("set"):
        c.set(_parse_dt(body["set"]))
    elif body.get("advance_minutes") is not None:
        c.set(c.now + timedelta(minutes=float(body["advance_minutes"])))
    else:
        raise ApiError(400, "INVALID_PARAMS", "需要 set 或 advance_minutes")
    return ok({"now": W.iso(c.now)})


@app.get("/control/faults", dependencies=[Depends(require_control)], tags=["控制"], summary="当前故障规则")
def control_faults():
    return ok({"items": STATE["faults"]})


@app.post("/control/faults", dependencies=[Depends(require_control)], tags=["控制"],
          summary="添加故障：{path, method?, kind: latency|http_429|http_503|timeout|timeout_after_commit, times?(0=不限), latency_ms?, hold_ms?}")
def control_add_fault(body: dict):
    if body.get("kind") not in ("latency", "http_429", "http_503", "timeout", "timeout_after_commit") or not body.get("path"):
        raise ApiError(400, "INVALID_PARAMS", "需要 path 和合法的 kind")
    STATE["faults"].append(dict(body))
    return ok({"items": STATE["faults"]})


@app.delete("/control/faults", dependencies=[Depends(require_control)], tags=["控制"], summary="清空故障")
def control_clear_faults():
    STATE["faults"] = []
    return ok({"items": []})


def _scenario_view(s: dict) -> dict:
    return s | {"clock": W.iso(s["clock"])}


@app.get("/control/scenarios", dependencies=[Depends(require_control)], tags=["控制"], summary="命名场景列表")
def control_scenarios():
    return ok({"items": [_scenario_view(s) for s in w().scenarios.values()]})


@app.post("/control/scenarios/{sid}/prepare", dependencies=[Depends(require_control)], tags=["控制"],
          summary="准备场景：重置数据，拨到场景时钟，注入场景故障")
def control_prepare(sid: str):
    wd = W.reset_world(seed=w().seed, anchor=w().anchor)
    s = wd.scenarios.get(sid)
    if not s:
        raise ApiError(404, "SCENARIO_NOT_FOUND", "场景不存在")
    wd.clock.set(s["clock"])
    STATE["faults"], STATE["idem"] = [dict(f) for f in s["faults"]], {}
    return ok(_scenario_view(s))


@app.post("/control/refunds/{refund_id}/settle", dependencies=[Depends(require_control)], tags=["控制"],
          summary="手动让退款成功或失败：{result: succeeded|failed, failure_code?}")
def control_settle(refund_id: str, body: dict):
    r = w().refunds.get(refund_id)
    if not r:
        raise ApiError(404, "REFUND_NOT_FOUND", "退款单不存在")
    if body.get("result") not in ("succeeded", "failed"):
        raise ApiError(400, "INVALID_PARAMS", "result 只能是 succeeded 或 failed")
    if r["status"] == "reviewing":
        r["status"] = "processing"
    r["forced_result"], r["failure_code"], r["due_at"] = body["result"], body.get("failure_code"), now()
    return ok(refund_view(r))


@app.post("/control/parcels/{mail_no}/deliver", dependencies=[Depends(require_control)], tags=["控制"], summary="让包裹立即签收")
def control_deliver(mail_no: str):
    wd = w()
    p = wd.parcels.get(mail_no)
    if not p:
        raise ApiError(404, "PARCEL_NOT_FOUND", "包裹不存在")
    p["traces"] = [t for t in p["traces"] if t["time"] <= now()] + [{"time": now(), "code": "40", "desc": "已签收，签收人：本人"}]
    p["outcome"] = "delivered"
    return ok({"mail_no": mail_no, "status": wd.parcel_state(p)["status"]})
