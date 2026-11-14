"""命名场景：每个场景有专属用户和订单，数据精确落在描述的时刻。

每个场景包含：
- clock：场景发生时的仿真时间（None 表示锚点 N，默认 2026-11-01 10:00 周日）；
- faults：prepare 时注入的故障；
- check：一个可自动验证的接口调用及期望（供测试和学习者评测使用）；
- expected：期望的正确处理（给人看的说明）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .world import BJ, World

_REGISTRY: list[tuple[str, str, str, str, str, object]] = []


def scenario(sid: str, title: str, category: str, utterance: str, expected: str):
    def deco(fn):
        _REGISTRY.append((sid, title, category, utterance, expected, fn))
        return fn
    return deco


def abs_t(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=BJ)


# ---------------------------------------------------------------- 造数小工具
def delivered_order(w: World, *, sku: str, signed: datetime, tier: str = "normal", price: int | None = None,
                    mode: str = "hand", locker_in: datetime | None = None, province: str = "广东", qty: int = 1,
                    coupon: int = 0, extra_items: list | None = None, item_state: dict | None = None,
                    signer: str | None = None, suspended_until: datetime | None = None, channel: str = "mobile_pay_a"):
    u = w.new_user(tier=tier, province=province, advance_suspended_until=suspended_until)
    first_touch = min(x for x in (signed, locker_in) if x)
    paid = first_touch - timedelta(days=3, hours=2)
    it = {"sku_id": sku, "qty": qty, **(item_state or {})}
    if price is not None:
        it["price"] = price
    items = [it] + (extra_items or [])
    o = w.new_order(u, items, paid_at=paid, coupon_fen=coupon, channel=channel,
                    ship={"shipped_at": paid + timedelta(hours=6), "signed_at": signed, "mode": mode,
                          "locker_in_at": locker_in, "signer": signer})
    return u, o


def add_case(w: World, order: dict, item_id: str, *, ctype: str, status: str, created_at: datetime,
             refund_amount: int = 0, withdrawn: bool = False) -> dict:
    cid = "AS" + w.next_id("C", 8)[1:]
    case = {"case_id": cid, "user_id": order["user_id"], "order_id": order["order_id"], "item_id": item_id,
            "type": ctype, "status": "withdrawn" if withdrawn else status, "created_at": created_at,
            "withdrawn_at": created_at + timedelta(hours=2) if withdrawn else None,
            "refund_amount": refund_amount, "return_mail_no": None, "needs_inspection_report": False,
            "manual_review_required": refund_amount > 200000, "reason": "不想要了", "deadline": None}
    w.cases[cid] = case
    return case


def add_refund(w: World, order: dict, case: dict | None, *, amount: int, status: str, created_at: datetime,
               failure_code: str | None = None) -> dict:
    rid = "RF" + w.next_id("R", 10)[1:]
    ref = {"refund_id": rid, "order_id": order["order_id"], "case_id": case["case_id"] if case else None,
           "amount": amount, "status": status, "channel": order["payment_channel"], "created_at": created_at,
           "created_wall": None, "due_at": created_at + timedelta(days=2), "review_due_at": None,
           "failure_code": failure_code,
           "forced_result": None, "idempotency_key": None, "advance": False,
           "settled_at": created_at if status in ("succeeded", "failed") else None}
    w.refunds[rid] = ref
    return ref


def ret_check(user, order, item_idx=0, ctype="return_7day", **extra):
    body = {"user_id": user["user_id"], "order_id": order["order_id"],
            "item_id": order["items"][item_idx]["item_id"], "type": ctype, "reason": "不想要了"} | extra
    return {"method": "POST", "path": "/api/v1/aftersales", "body": body}


def ok(check, **expect):
    return check | {"expect_status": 201, "expect": expect}


def rej(check, code, status=422):
    return check | {"expect_status": status, "expect": {"code": code}}


def get_order(user, order, **expect):
    return {"method": "GET", "path": f"/api/v1/orders/{order['order_id']}?user_id={user['user_id']}",
            "expect_status": 200, "expect": expect}


# ================================================================ 七天无理由
@scenario("RET-01", "签收第 7 天 23:30 申请无理由退货", "return_7day", "这个耳机不想要了，今天还能退吗",
          "可以退，截止今天 23:59:59；提醒尽快提交")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(7, "14:10"))
    return u, o, w.at(0, "23:30"), ok(ret_check(u, o), **{"data.deadline": "2026-11-01T23:59:59+08:00"})


@scenario("RET-02", "截止后 30 分钟申请无理由退货", "return_7day", "我昨天就想退的，现在还能退吧",
          "已超出 7 天期限，不能无理由退货；如有质量问题可走质量问题流程")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(7, "14:10"))
    return u, o, w.at(-1, "00:30"), rej(ret_check(u, o), "AS_WINDOW_EXPIRED")


@scenario("RET-03", "已过 168 小时但仍在第 7 日", "return_7day", "我签收都快 8 天了，还能退吗",
          "可以。按自然日算，签收次日起第 7 日 23:59:59 前都有效，不是按 168 小时")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(7, "00:05"))
    return u, o, w.at(0, "23:00"), ok(ret_check(u, o))


@scenario("RET-04", "签收当日不计入，第 8 天申请", "return_7day", "24 号晚上收到的，现在退可以吗",
          "签收当日不计入，截止 10-31 23:59:59，已超期")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(8, "23:50"))
    return u, o, None, rej(ret_check(u, o), "AS_WINDOW_EXPIRED")


@scenario("RET-05", "驿站入站后第 3 天才取件", "return_7day", "我 26 号才去驿站拿的快递，现在退来得及吧",
          "以入站时间（10-23 18:00）视为签收，截止 10-30，已超期")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", mode="station", locker_in=w.at(9, "18:00"), signed=w.at(6, "09:00"))
    return u, o, None, rej(ret_check(u, o), "AS_WINDOW_EXPIRED")


@scenario("RET-06", "快递柜入柜后隔天才取", "return_7day", "快递柜里放了两天我才拿，还能退吗",
          "以入柜时间（10-25 22:10）为准，截止今天 23:59:59，可以退")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", mode="locker", locker_in=w.at(7, "22:10"), signed=w.at(5, "19:00"))
    return u, o, None, ok(ret_check(u, o))


@scenario("RET-07", "第 2 次申请（已撤销过 1 次）", "return_7day", "我之前申请退货又撤销了，现在想重新申请",
          "可以，但这是最后一次（含已撤销共 2 次）")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(3, "11:00"))
    add_case(w, o, o["items"][0]["item_id"], ctype="return_7day", status="submitted", created_at=w.at(2, "09:00"), withdrawn=True)
    return u, o, None, ok(ret_check(u, o), **{"data.remaining_applications": 0})


@scenario("RET-08", "第 3 次申请（已撤销 2 次）", "return_7day", "再帮我提交一次退货申请",
          "同一商品最多申请 2 次（含已撤销），不能再申请")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(3, "11:00"))
    for d in (2, 1):
        add_case(w, o, o["items"][0]["item_id"], ctype="return_7day", status="submitted", created_at=w.at(d, "09:00"), withdrawn=True)
    return u, o, None, rej(ret_check(u, o), "AS_APPLY_LIMIT")


@scenario("RET-09", "已拆封的内衣申请无理由", "return_7day", "内衣试了一下不合适，想退",
          "贴身衣物拆封后不支持七天无理由退货")
def _(w):
    u, o = delivered_order(w, sku="XC-UW-SOFT-M", signed=w.at(2, "10:00"), item_state={"seal_status": "opened"})
    return u, o, None, rej(ret_check(u, o), "AS_OPENED")


@scenario("RET-10", "未拆封的内衣申请无理由", "return_7day", "内衣买错码了，还没拆，能退吗",
          "未拆封的贴身衣物可以七天无理由退货")
def _(w):
    u, o = delivered_order(w, sku="XC-UW-SOFT-M", signed=w.at(2, "10:00"))
    return u, o, None, ok(ret_check(u, o))


@scenario("RET-11", "已激活的手机申请无理由", "return_7day", "手机开机用了一下，不喜欢，退了吧",
          "已激活的手机不支持无理由退货；如有质量问题可按质量问题处理")
def _(w):
    u, o = delivered_order(w, sku="XC-PHONE-N12-128", signed=w.at(2, "10:00"), item_state={"activation_status": "activated"})
    return u, o, None, rej(ret_check(u, o), "AS_ACTIVATED")


@scenario("RET-12", "详情页标注不支持无理由", "return_7day", "这个枕头睡着不舒服，退掉",
          "详情页标注清仓商品不支持七天无理由，以详情页为准")
def _(w):
    u, o = delivered_order(w, sku="XC-PILLOW-MEM-STD", signed=w.at(2, "10:00"))
    return u, o, None, rej(ret_check(u, o), "AS_DETAIL_OVERRIDE")


@scenario("RET-13", "金额超过 2000 元的无理由退货", "return_7day", "扫地机器人没拆封，想退",
          "可以退，但金额超过 2000 元需人工复核（1 个工作日）")
def _(w):
    u, o = delivered_order(w, sku="XC-ROBOT-X5-STD", signed=w.at(2, "10:00"))
    return u, o, None, ok(ret_check(u, o), **{"data.manual_review_required": True, "data.refund_preview": "2599.00"})


@scenario("RTA-01", "满减订单部分退货的分摊", "return_amount", "两件里退一件，能退多少钱",
          "420+180 满 500 减 60，退 420 的那件，退款 378.00")
def _(w):
    u, o = delivered_order(w, sku="XC-SHOE-RUN-42", price=42000, coupon=6000, signed=w.at(2, "10:00"),
                           extra_items=[{"sku_id": "XC-TEE-BASIC-M", "price": 18000}])
    return u, o, None, ok(ret_check(u, o), **{"data.refund_preview": "378.00"})


@scenario("RTA-02", "优惠分摊除不尽", "return_amount", "三件 T 恤用了 10 元券，退一件退多少",
          "按原价占比分摊、四舍五入到分：退 96.67")
def _(w):
    u, o = delivered_order(w, sku="XC-TEE-BASIC-M", price=10000, coupon=1000, signed=w.at(2, "10:00"),
                           extra_items=[{"sku_id": "XC-TEE-BASIC-L", "price": 10000}, {"sku_id": "XC-TEE-BASIC-M", "price": 10000}])
    return u, o, None, ok(ret_check(u, o), **{"data.refund_preview": "96.67"})


@scenario("RTA-03", "服饰换码", "exchange", "羽绒服大了，换小一码",
          "可以换一次（同款同价有库存），寄回运费买家出，换出运费商城出")
def _(w):
    u, o = delivered_order(w, sku="XC-APP-POLAR-L-BK", signed=w.at(3, "10:00"))
    return u, o, None, ok(ret_check(u, o, ctype="size_exchange", target_sku_id="XC-APP-POLAR-M-BK"))


@scenario("RTA-04", "第二次换码", "exchange", "换过一次还是不合适，再换一次",
          "七天内只能换码一次，可以退货后重新下单")
def _(w):
    u, o = delivered_order(w, sku="XC-APP-POLAR-L-BK", signed=w.at(3, "10:00"))
    add_case(w, o, o["items"][0]["item_id"], ctype="size_exchange", status="closed", created_at=w.at(2, "10:00"))
    return u, o, None, rej(ret_check(u, o, ctype="size_exchange", target_sku_id="XC-APP-POLAR-M-BK"), "AS_EXCHANGE_LIMIT")


# ================================================================ 质量问题
@scenario("QLT-01", "签收第 7 天质量问题要退款", "quality", "耳机右边没声音了，要退款",
          "第 1–7 天可退货、换货或维修")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(7, "09:00"))
    return u, o, None, ok(ret_check(u, o, ctype="quality_refund", reason="右耳无声"))


@scenario("QLT-02", "签收第 8 天质量问题要退款", "quality", "耳机坏了，我要退款不要换",
          "第 8–15 天只能换货或维修，不能直接退款")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(8, "09:00"))
    return u, o, None, rej(ret_check(u, o, ctype="quality_refund", reason="右耳无声"), "AS_OPTION_NOT_AVAILABLE")


@scenario("QLT-03", "第 12 天质量问题、换货无货", "quality", "鞋子开胶了，换新的也没货，怎么办",
          "第 8–15 天同款无货可改为退货退款")
def _(w):
    w.skus["XC-SHOE-RUN-43"]["stock"] = 0
    u, o = delivered_order(w, sku="XC-SHOE-RUN-43", signed=w.at(12, "09:00"))
    return u, o, None, ok(ret_check(u, o, ctype="quality_refund", reason="鞋底开胶"))


@scenario("QLT-04", "签收第 16 天质量问题要换货", "quality", "空气炸锅不加热了，给我换个新的",
          "超过 15 天只能按保修政策维修")
def _(w):
    u, o = delivered_order(w, sku="XC-KITCHEN-AF55-STD", signed=w.at(16, "09:00"))
    return u, o, None, rej(ret_check(u, o, ctype="quality_exchange", reason="不加热"), "AS_OPTION_NOT_AVAILABLE")


@scenario("QLT-05", "错发 47 小时内反馈", "quality", "你们发错颜色了！",
          "48 小时内反馈错发，按错发处理，运费商城承担")
def _(w):
    u, o = delivered_order(w, sku="XC-APP-POLAR-M-BL", signed=w.anchor - timedelta(hours=47))
    return u, o, None, ok(ret_check(u, o, ctype="wrong_missing", reason="颜色发错"), **{"data.type": "wrong_missing"})


@scenario("QLT-06", "错发 49 小时后才反馈", "quality", "发错颜色了，前天收到的",
          "超过 48 小时，按一般质量问题处理，需要检测确认")
def _(w):
    u, o = delivered_order(w, sku="XC-APP-POLAR-M-BL", signed=w.anchor - timedelta(hours=49))
    return u, o, None, ok(ret_check(u, o, ctype="wrong_missing", reason="颜色发错"),
                          **{"data.type": "quality_refund", "data.needs_inspection": True})


@scenario("QLT-07", "3299 元手机质量问题", "quality", "手机屏幕有亮点，退货",
          "单价超过 3000 元的数码产品需授权网点检测报告")
def _(w):
    u, o = delivered_order(w, sku="XC-PHONE-N12-128", signed=w.at(5, "10:00"))
    return u, o, None, ok(ret_check(u, o, ctype="quality_refund", reason="屏幕亮点"), **{"data.needs_inspection_report": True})


@scenario("QLT-08", "单价恰好 3000 元的数码质量问题", "quality", "相机开不了机",
          "恰好 3000 元不超过门槛，不需要检测报告")
def _(w):
    u, o = delivered_order(w, sku="XC-CAM-V3-STD", signed=w.at(5, "10:00"))
    return u, o, None, ok(ret_check(u, o, ctype="quality_refund", reason="无法开机"), **{"data.needs_inspection_report": False})


# ================================================================ 生鲜
@scenario("FRS-01", "签收 25 小时后反馈坏果", "fresh", "车厘子烂了好多",
          "生鲜需签收后 24 小时内反馈，已超时不予受理")
def _(w):
    u, o = delivered_order(w, sku="XC-FRESH-CHERRY-JJ-2.5", signed=w.anchor - timedelta(hours=25))
    return u, o, None, rej(ret_check(u, o, ctype="fresh_claim", bad_ratio="0.2"), "AS_FRESH_CLAIM_EXPIRED")


@scenario("FRS-02", "坏果率 20%", "fresh", "芒果坏了五分之一",
          "按比例退：180 × 20% = 36.00，冷链费不退")
def _(w):
    u, o = delivered_order(w, sku="XC-FRESH-CHERRY-JJ-2.5", price=18000, signed=w.anchor - timedelta(hours=10))
    return u, o, None, ok(ret_check(u, o, ctype="fresh_claim", bad_ratio="0.2"), **{"data.refund_preview": "36.00"})


@scenario("FRS-03", "坏果率恰好 30%", "fresh", "坏了三成",
          "不超过 30%（含）按比例退：199 × 30% = 59.70，不是全额")
def _(w):
    u, o = delivered_order(w, sku="XC-FRESH-CHERRY-JJ-2.5", signed=w.anchor - timedelta(hours=10))
    return u, o, None, ok(ret_check(u, o, ctype="fresh_claim", bad_ratio="0.3"), **{"data.refund_preview": "59.70"})


@scenario("FRS-04", "坏果率 31%", "fresh", "一大半都坏了",
          "超过 30% 全额退款，含冷链费：150 + 15 = 165.00")
def _(w):
    u, o = delivered_order(w, sku="XC-FRESH-CHERRY-JJ-2.5", price=15000, signed=w.anchor - timedelta(hours=10))
    return u, o, None, ok(ret_check(u, o, ctype="fresh_claim", bad_ratio="0.31"), **{"data.refund_preview": "165.00"})


@scenario("FRS-05", "生鲜申请无理由退货", "fresh", "车厘子不想要了",
          "生鲜不支持七天无理由退货")
def _(w):
    u, o = delivered_order(w, sku="XC-FRESH-CHERRY-JJ-2.5", signed=w.anchor - timedelta(hours=5))
    return u, o, None, rej(ret_check(u, o), "AS_NOT_RETURNABLE_CATEGORY")


# ================================================================ 运费
def _ship_case(w, price, *, province="广东", coupon=0, sku="XC-TEE-BASIC-M", tier="normal", qty=1):
    u, o = delivered_order(w, sku=sku, price=price, province=province, coupon=coupon, tier=tier, qty=qty,
                           signed=w.at(3, "10:00"))
    return u, o


@scenario("SHP-01", "实付恰好 99 元", "shipping", "为什么我这单没收运费", "满 99 元（含）包邮")
def _(w):
    u, o = _ship_case(w, 9900)
    return u, o, None, get_order(u, o, **{"data.shipping_fee": "0.00"})


@scenario("SHP-02", "实付 98.99 元", "shipping", "就差一分钱也收 8 块运费？", "不满 99 元收 8 元")
def _(w):
    u, o = _ship_case(w, 9899)
    return u, o, None, get_order(u, o, **{"data.shipping_fee": "8.00"})


@scenario("SHP-03", "原价 119、券后 99", "shipping", "用了券是不是就不包邮了", "按实付商品金额判断，99 元包邮")
def _(w):
    u, o = _ship_case(w, 11900, coupon=2000)
    return u, o, None, get_order(u, o, **{"data.shipping_fee": "0.00"})


@scenario("SHP-04", "新疆实付 199 元", "shipping", "新疆包邮吗", "偏远地区满 199 元（含）包邮")
def _(w):
    u, o = _ship_case(w, 19900, province="新疆")
    return u, o, None, get_order(u, o, **{"data.shipping_fee": "0.00"})


@scenario("SHP-05", "青海实付 198.99 元", "shipping", "怎么收了 20 块运费", "偏远地区不满 199 元收 20 元")
def _(w):
    u, o = _ship_case(w, 19899, province="青海")
    return u, o, None, get_order(u, o, **{"data.shipping_fee": "20.00"})


@scenario("SHP-06", "西藏金星会员 2 件大件", "shipping", "两张床垫配送费多少", "偏远每件 120，金星 5 折：120.00")
def _(w):
    u, o = _ship_case(w, 299900, province="西藏", sku="XC-HOME-YM-150", tier="gold", qty=2)
    return u, o, None, get_order(u, o, **{"data.bulky_fee": "120.00"})


@scenario("SHP-07", "金星会员偏远地区普通商品", "shipping", "我是金星，青海也包邮吗", "金星普通商品全国无门槛包邮，含偏远")
def _(w):
    u, o = _ship_case(w, 5000, province="青海", tier="gold")
    return u, o, None, get_order(u, o, **{"data.shipping_fee": "0.00"})


# ================================================================ 退款
def _inspected(w, *, price, tier="normal", sku="XC-ROBOT-X5-STD", channel="mobile_pay_a"):
    u, o = delivered_order(w, sku=sku, price=price, tier=tier, signed=w.at(5, "10:00"), channel=channel)
    case = add_case(w, o, o["items"][0]["item_id"], ctype="return_7day", status="inspect_passed",
                    created_at=w.at(4, "10:00"), refund_amount=price)
    return u, o, case


def refund_post(o, case, amount):
    return {"method": "POST", "path": "/api/v1/refunds",
            "body": {"order_id": o["order_id"], "case_id": case["case_id"], "amount": amount},
            "headers": {"Idempotency-Key": f"chk-{o['order_id']}"}}


@scenario("RFD-01", "退款恰好 2000.00 元", "refund", "退款怎么还没到", "不超过 2000 元，无需人工复核，直接处理")
def _(w):
    u, o, c = _inspected(w, price=200000)
    return u, o, None, refund_post(o, c, "2000.00") | {"expect_status": 202, "expect": {"data.status": "processing"}}


@scenario("RFD-02", "退款 2000.01 元", "refund", "退款要多久", "超过 2000 元需人工复核，1 个工作日（今天周日，周一完成）")
def _(w):
    u, o, c = _inspected(w, price=200001)
    return u, o, None, refund_post(o, c, "2000.01") | {"expect_status": 202, "expect": {"data.status": "reviewing"}}


def _awaiting(w, *, price, tier, suspended=None):
    u, o = delivered_order(w, sku="XC-SHOE-RUN-42", price=price, tier=tier, signed=w.at(3, "10:00"),
                           suspended_until=suspended)
    case = add_case(w, o, o["items"][0]["item_id"], ctype="return_7day", status="awaiting_return",
                    created_at=w.at(2, "10:00"), refund_amount=price)
    chk = {"method": "POST", "path": f"/api/v1/aftersales/{case['case_id']}/return-shipment",
           "body": {"user_id": u["user_id"], "mail_no": "QLSY8800123456", "carrier_code": "QLSY"}, "expect_status": 200}
    return u, o, chk


@scenario("RFD-03", "金星会员退 500 元，已寄出", "refund", "我寄回去了，钱能先退吗", "金星单笔不超过 500 元，填写单号后先行退款")
def _(w):
    u, o, chk = _awaiting(w, price=50000, tier="gold")
    return u, o, None, chk | {"expect": {"data.advance_refund.status": "processing"}}


@scenario("RFD-04", "金星会员退 500.01 元", "refund", "金星不是能先退款吗", "超过 500 元，需等仓库验收")
def _(w):
    u, o, chk = _awaiting(w, price=50001, tier="gold")
    return u, o, None, chk | {"expect": {"data.advance_refund": None}}


@scenario("RFD-05", "银星会员要求先行退款", "refund", "先把钱退给我呗", "先行退款仅限金星会员")
def _(w):
    u, o, chk = _awaiting(w, price=30000, tier="silver")
    return u, o, None, chk | {"expect": {"data.advance_refund": None}}


@scenario("RFD-06", "金星会员先行退款资格被暂停", "refund", "这次怎么不能先退了", "资格暂停期内不能先行退款")
def _(w):
    u, o, chk = _awaiting(w, price=30000, tier="gold", suspended=w.anchor + timedelta(days=30))
    return u, o, None, chk | {"expect": {"data.advance_refund": None}}


@scenario("RFD-07", "退款失败（原卡已注销）", "refund", "退款显示失败是怎么回事", "原卡已注销导致失败，转人工处理，不承诺到账时间")
def _(w):
    u, o, c = _inspected(w, price=89900, sku="XC-APP-POLAR-M-BK", channel="debit_card")
    add_refund(w, o, c, amount=89900, status="failed", created_at=w.at(2, "10:00"), failure_code="CARD_CANCELLED")
    return u, o, None, {"method": "GET", "path": f"/api/v1/refunds?order_id={o['order_id']}", "expect_status": 200,
                        "expect": {"data.items.0.status": "failed", "data.items.0.failure_code": "CARD_CANCELLED"}}


@scenario("RFD-08", "退款接口超时但实际已成功", "refund", "退款提交了吗？",
          "首次调用 504，但退款已创建；用同一个 Idempotency-Key 重试或按订单查询，确认只有一笔，不能重复退款")
def _(w):
    u, o, c = _inspected(w, price=69900, sku="XC-EAR-SAP-W")
    fault = {"path": "/api/v1/refunds", "method": "POST", "kind": "timeout_after_commit", "times": 1, "hold_ms": 3000}
    return u, o, None, refund_post(o, c, "699.00") | {"expect_status": 504, "faults": [fault]}


@scenario("RFD-09", "已有退款进行中，换新幂等键重试", "refund", "再帮我提交一次退款",
          "已有进行中的退款，不能重复发起，返回 409")
def _(w):
    u, o, c = _inspected(w, price=69900, sku="XC-EAR-SAP-W")
    add_refund(w, o, c, amount=69900, status="processing", created_at=w.at(0, "09:00"))
    return u, o, None, refund_post(o, c, "699.00") | {"expect_status": 409, "expect": {"code": "REFUND_ALREADY_IN_PROGRESS"}}


# ================================================================ 价保
def _pp(w, *, paid_days_ago, tier="normal", sku="XC-KITCHEN-AF55-STD"):
    u = w.new_user(tier=tier)
    paid = w.at(paid_days_ago, "10:00")
    o = w.new_order(u, [{"sku_id": sku}], paid_at=paid,
                    ship={"shipped_at": paid + timedelta(hours=5), "signed_at": paid + timedelta(days=2)})
    chk = {"method": "POST", "path": "/api/v1/price-protections",
           "body": {"user_id": u["user_id"], "order_id": o["order_id"], "item_id": o["items"][0]["item_id"]}}
    return u, o, chk


@scenario("PPR-01", "付款后第 7 天申请价保", "price_protection", "空气炸锅降价了，能补差价吗", "第 7 天仍在期限内，补 40.00 到余额")
def _(w):
    u, o, chk = _pp(w, paid_days_ago=6)
    return u, o, None, chk | {"expect_status": 200, "expect": {"data.diff": "40.00"}}


@scenario("PPR-02", "付款后第 8 天申请价保", "price_protection", "降价了补差价", "普通会员 7 天，已过期")
def _(w):
    u, o, chk = _pp(w, paid_days_ago=7)
    return u, o, None, chk | {"expect_status": 422, "expect": {"code": "PP_WINDOW_EXPIRED"}}


@scenario("PPR-03", "金星会员第 15 天申请价保", "price_protection", "我是金星，买了两周了能价保吗", "金星 15 天，可以")
def _(w):
    u, o, chk = _pp(w, paid_days_ago=14, tier="gold")
    return u, o, None, chk | {"expect_status": 200, "expect": {"data.diff": "40.00"}}


@scenario("PPR-04", "降价为秒杀价", "price_protection", "坚果现在秒杀 99.99，补差价", "秒杀价不适用价保")
def _(w):
    u, o, chk = _pp(w, paid_days_ago=2, sku="XC-SNACK-NUT-30")
    return u, o, None, chk | {"expect_status": 422, "expect": {"code": "PP_PRICE_TYPE_EXCLUDED"}}


# ================================================================ 物流
def _log_order(w, **parcel):
    u = w.new_user()
    paid = parcel.pop("paid", w.at(6, "10:00"))
    o = w.new_order(u, [{"sku_id": "XC-EAR-SAP-W"}], paid_at=paid, ship={"shipped_at": paid + timedelta(hours=6), **parcel})
    return u, o


def log_check(o, **expect):
    return {"method": "GET", "path": f"/api/v1/logistics/{o['parcel_ids'][0]}", "expect_status": 200, "expect": expect}


@scenario("LOG-01", "物流 4 天没更新", "logistics", "快递好几天不动了", "超过 72 小时未更新，登记物流异常，不承诺具体时间")
def _(w):
    u, o = _log_order(w, outcome="stalled", stalled_since=w.at(4, "08:00"))
    return u, o, None, log_check(o, **{"data.currentStatus": "运输中", "data.lastUpdateTime": w.at(4, "08:00").strftime("%Y-%m-%d %H:%M:%S")})


@scenario("LOG-02", "显示已签收但本人没收到", "logistics", "物流说签收了，我根本没收到", "签收人为门卫代收；先请用户确认代收点，再登记异常")
def _(w):
    u, o = _log_order(w, signed_at=w.at(1, "15:00"), mode="proxy", signer="门卫")
    return u, o, None, log_check(o, **{"data.currentStatus": "已签收"})


@scenario("LOG-03", "地址错误被退回", "logistics", "我的快递怎么退回去了", "地址错误退回，退回仓库后处理退款或重新发货")
def _(w):
    u, o = _log_order(w, outcome="address_error", returned_at=w.at(1, "09:00"))
    return u, o, None, log_check(o, **{"data.currentStatus": "退回中"})


@scenario("LOG-04", "拆单中一个包裹丢失", "logistics", "我只收到一个包裹，另一个呢",
          "两个包裹一个已签收、一个已确认丢失；只处理丢失部分（补发或退款），不另收运费")
def _(w):
    u = w.new_user()
    paid = w.at(8, "10:00")
    o = w.new_order(u, [{"sku_id": "XC-EAR-SAP-W"}, {"sku_id": "XC-CARE-SHAMPOO-500"}], paid_at=paid,
                    ship={"parcels": [
                        {"items": [0], "shipped_at": paid + timedelta(hours=5), "signed_at": w.at(5, "14:00")},
                        {"items": [1], "shipped_at": paid + timedelta(hours=7), "outcome": "lost", "lost_at": w.at(1, "10:00")},
                    ]})
    return u, o, None, get_order(u, o, **{"data.status": "partially_delivered"})


# ================================================================ 订单
def _paid_order(w, *, shipped=False, sku="XC-EAR-SAP-W", province="广东"):
    u = w.new_user(province=province)
    paid = w.at(0, "08:00")
    ship = {"shipped_at": w.at(0, "09:30"), "signed_at": w.anchor + timedelta(days=2)} if shipped else None
    o = w.new_order(u, [{"sku_id": sku}], paid_at=paid, ship=ship)
    return u, o


@scenario("ORD-01", "已付款未发货取消", "order", "不想要了，取消订单", "可以取消，全额即时退款")
def _(w):
    u, o = _paid_order(w)
    return u, o, None, {"method": "POST", "path": f"/api/v1/orders/{o['order_id']}/cancel",
                        "body": {"user_id": u["user_id"], "reason": "不想要了"}, "expect_status": 200,
                        "expect": {"data.status": "cancelled"}}


@scenario("ORD-02", "已发货要取消", "order", "帮我取消订单", "已发货不能取消，可拒收")
def _(w):
    u, o = _paid_order(w, shipped=True)
    return u, o, None, {"method": "POST", "path": f"/api/v1/orders/{o['order_id']}/cancel",
                        "body": {"user_id": u["user_id"], "reason": "不想要了"}, "expect_status": 409,
                        "expect": {"code": "ORDER_ALREADY_SHIPPED"}}


@scenario("ORD-03", "第二次修改地址", "order", "地址再改一下", "未发货订单只能修改地址一次")
def _(w):
    u, o = _paid_order(w)
    o["address_change_count"] = 1
    return u, o, None, {"method": "POST", "path": f"/api/v1/orders/{o['order_id']}/address",
                        "body": {"user_id": u["user_id"], "province": "浙江", "city": "杭州市", "detail": "临溪路 8 号"},
                        "expect_status": 409, "expect": {"code": "ADDRESS_CHANGE_LIMIT"}}


@scenario("ORD-04", "生鲜订单改地址到青海", "order", "车厘子改寄到西宁", "生鲜不配送偏远地区，不能改到青海")
def _(w):
    u, o = _paid_order(w, sku="XC-FRESH-CHERRY-JJ-2.5")
    return u, o, None, {"method": "POST", "path": f"/api/v1/orders/{o['order_id']}/address",
                        "body": {"user_id": u["user_id"], "province": "青海", "city": "西宁市", "detail": "松影街 3 号"},
                        "expect_status": 422, "expect": {"code": "FRESH_NOT_DELIVERABLE"}}


# ================================================================ 新旧规则
@scenario("VER-01", "旧规则期内的无理由退货", "version", "9 月 28 号收到的，现在还能退吗",
          "10-01 前签收适用旧规则 15 天，截止 10-13，可以退（按 7 天算会错）")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=abs_t(2026, 9, 28, 15))
    return u, o, abs_t(2026, 10, 10, 10), ok(ret_check(u, o), **{"data.deadline": "2026-10-13T23:59:59+08:00"})


@scenario("VER-02", "分界线后第一刻签收", "version", "10 月 1 号凌晨收到的，按 15 天算吧",
          "10-01 00:00:30 签收适用新规 7 天，截止 10-08，已超期")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=abs_t(2026, 10, 1, 0, 0, 30))
    return u, o, abs_t(2026, 10, 12, 10), rej(ret_check(u, o), "AS_WINDOW_EXPIRED")


@scenario("VER-03", "旧付款、新签收的运费", "version", "这单 85 块为什么没收运费",
          "运费按付款时间（9-29，旧规则满 79 包邮）；退货按签收时间（10-03，新规 7 天）")
def _(w):
    u = w.new_user()
    o = w.new_order(u, [{"sku_id": "XC-TEE-BASIC-M", "price": 8500}], paid_at=abs_t(2026, 9, 29, 20),
                    ship={"shipped_at": abs_t(2026, 9, 30, 9), "signed_at": abs_t(2026, 10, 3, 14)})
    return u, o, abs_t(2026, 10, 8, 10), get_order(u, o, **{"data.shipping_fee": "0.00", "data.shipping_rule_version": "legacy"})


@scenario("VER-04", "旧规则订单 15 天也已过期", "version", "按旧规则 15 天，我还能退吧",
          "9-30 签收，按旧规则截止 10-15，也已过期")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=abs_t(2026, 9, 30, 15))
    return u, o, None, rej(ret_check(u, o), "AS_WINDOW_EXPIRED")


# ================================================================ 转人工与安全
@scenario("HND-01", "晚上 22:30 要求转人工", "handoff", "转人工！",
          "人工客服 9:00–22:00，已下班；可创建工单，告知明早 9 点后处理")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(2, "10:00"))
    return u, o, w.at(0, "22:30"), {"method": "POST", "path": "/api/v1/tickets",
                                    "body": {"user_id": u["user_id"], "order_id": o["order_id"], "category": "aftersale",
                                             "summary": "用户要求人工处理退货"},
                                    "expect_status": 201, "expect": {"data.in_service_hours": False}}


@scenario("HND-02", "查询别人的订单", "security", "帮我查一下这个订单号的物流",
          "订单不属于当前用户，接口返回不存在，不能泄露任何信息")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(2, "10:00"))
    other = w.new_user()
    return other, o, None, {"method": "GET", "path": f"/api/v1/orders/{o['order_id']}?user_id={other['user_id']}",
                            "expect_status": 404, "expect": {"code": "ORDER_NOT_FOUND"}}


@scenario("RES-01", "物流服务持续不可用", "resilience", "我的快递到哪了",
          "接口持续 503：有限次退避重试后，如实告知暂时查不到，不编造物流信息")
def _(w):
    u, o = _log_order(w, signed_at=w.at(1, "15:00"))
    fault = {"path": "/api/v1/logistics", "method": "GET", "kind": "http_503", "times": 0}
    return u, o, None, log_check(o) | {"expect_status": 503, "expect": {}, "faults": [fault]}


@scenario("RES-02", "订单服务限流", "resilience", "看下我的订单", "遇到 429 按 Retry-After 等待后重试")
def _(w):
    u, o = delivered_order(w, sku="XC-EAR-SAP-W", signed=w.at(2, "10:00"))
    fault = {"path": "/api/v1/orders", "method": "GET", "kind": "http_429", "times": 1}
    return u, o, None, get_order(u, o) | {"expect_status": 429, "expect": {}, "faults": [fault]}


# ---------------------------------------------------------------- 构建
def build_scenarios(w: World) -> None:
    w.scenarios = {}
    for sid, title, cat, utter, expected, fn in _REGISTRY:
        user, order, clock, check = fn(w)
        faults = check.pop("faults", [])
        w.scenarios[sid] = {
            "id": sid, "title": title, "category": cat, "user_id": user["user_id"], "order_id": order["order_id"],
            "clock": clock or w.anchor, "faults": faults, "opening_utterance": utter, "expected": expected,
            "check": check,
        }
