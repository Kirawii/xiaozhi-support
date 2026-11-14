"""内存数据世界：仿真时钟、实体存储、造数工具和“按时钟推导”的状态。

设计要点（简化版）：
- 所有数据在内存里，`World.build(seed, anchor)` 确定性地重建；重置就是重建。
- 物流轨迹预先生成到未来，按仿真时钟过滤可见部分；订单状态、签收时间都由可见轨迹推导。
  因此拨动时钟后，订单/物流/售后的状态自然一致，不需要同步任务。
- 金额一律整数分；时间一律北京时间 aware datetime。
"""
from __future__ import annotations

import hashlib
import json
import random
import time as _walltime
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from xcsim.policy import ShipLine, allocate_discount, business_signed_at, calc_shipping, is_remote_province
from xcsim.policy.timeutil import tz

from .catalog import GIFT_SKU, build_catalog

BJ = tz()
DEFAULT_ANCHOR = datetime(2026, 11, 1, 10, 0, tzinfo=BJ)

# 物流状态码（模拟第三方“青鸾速运”的数字编码）
TRACE_CODES = {
    "10": "已揽收", "20": "运输中", "30": "派送中", "35": "已入柜/驿站代收",
    "40": "已签收", "50": "异常", "60": "退回中", "61": "已退回",
}
CARRIERS = {"normal": ("QLSY", "青鸾速运"), "bulky": ("XNDJ", "玄鸟大件"), "fresh": ("SLLY", "霜链冷运")}

_SURNAMES = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
_GIVEN = "伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华建国志强晓东海燕玉兰子涵雨轩浩然欣怡梓萱思远佳怡一鸣若曦"
_PROVINCES = [  # (省, 市, 权重)
    ("广东", "深圳市", 14), ("江苏", "苏州市", 9), ("浙江", "杭州市", 9), ("山东", "青岛市", 7), ("河南", "郑州市", 5),
    ("四川", "成都市", 5), ("上海", "上海市", 5), ("北京", "北京市", 5), ("湖北", "武汉市", 4), ("福建", "厦门市", 4),
    ("湖南", "长沙市", 4), ("河北", "石家庄市", 3), ("安徽", "合肥市", 3), ("陕西", "西安市", 3), ("辽宁", "大连市", 2),
    ("重庆", "重庆市", 2), ("江西", "南昌市", 2), ("广西", "南宁市", 2), ("云南", "昆明市", 2), ("天津", "天津市", 2),
    ("山西", "太原市", 1), ("贵州", "贵阳市", 1), ("吉林", "长春市", 1), ("黑龙江", "哈尔滨市", 1), ("内蒙古", "呼和浩特市", 1),
    ("甘肃", "兰州市", 1), ("海南", "海口市", 1), ("宁夏", "银川市", 1), ("新疆", "乌鲁木齐市", 1), ("西藏", "拉萨市", 1),
    ("青海", "西宁市", 1),
]
_STREETS = ["星河路", "云栖街", "望川大道", "青禾巷", "临溪路", "松影街", "明湖路", "栖霞里"]


def yuan(fen: int) -> str:
    return f"{fen / 100:.2f}"


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(BJ).isoformat(timespec="seconds") if dt else None


def bjstr(dt: datetime | None) -> str | None:
    return dt.astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S") if dt else None


@dataclass
class Clock:
    now: datetime = DEFAULT_ANCHOR

    def set(self, dt: datetime) -> None:
        self.now = dt.astimezone(BJ)


@dataclass
class World:
    anchor: datetime
    seed: int
    clock: Clock
    spus: dict = field(default_factory=dict)
    skus: dict = field(default_factory=dict)
    users: dict = field(default_factory=dict)
    orders: dict = field(default_factory=dict)
    parcels: dict = field(default_factory=dict)
    cases: dict = field(default_factory=dict)
    refunds: dict = field(default_factory=dict)
    tickets: dict = field(default_factory=dict)
    price_claims: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)

    # ------------------------------------------------------------ 基础
    @classmethod
    def build(cls, seed: int = 42, anchor: datetime = DEFAULT_ANCHOR, background: bool = True) -> "World":
        from .scenarios import build_scenarios  # 避免循环导入

        w = cls(anchor=anchor.astimezone(BJ), seed=seed, clock=Clock(anchor.astimezone(BJ)))
        w.rng = random.Random(seed)
        w.spus, w.skus = build_catalog()
        build_scenarios(w)  # 先造场景数据，保证场景 ID 与编号在不同规模下稳定
        if background:
            w._background(n_users=300, n_orders=1500)
        return w

    def next_id(self, prefix: str, width: int = 6) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}{n:0{width}d}"

    def at(self, days_ago: float, hhmm: str | None = None) -> datetime:
        """相对锚点 N 的时刻。days_ago 为整数且给出 hhmm 时，取“N 所在日减 days_ago 天”的该时刻。"""
        if hhmm is None:
            return self.anchor - timedelta(days=days_ago)
        h, m = (int(x) for x in hhmm.split(":"))
        d = (self.anchor - timedelta(days=days_ago)).date()
        return datetime(d.year, d.month, d.day, h, m, tzinfo=BJ)

    def fingerprint(self) -> str:
        """数据内容哈希（确定性检查用）。"""
        payload = {k: getattr(self, k) for k in ("users", "orders", "parcels", "cases", "refunds", "tickets")}
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ 造数
    def new_user(self, *, tier: str = "normal", province: str = "广东", city: str | None = None,
                 name: str | None = None, points: int = 0, advance_suspended_until: datetime | None = None,
                 has_elevator: bool = True, floor: int = 3) -> dict:
        uid = "U" + str(1040000 + len(self.users) + 1)
        r = self.rng
        name = name or r.choice(_SURNAMES) + "".join(r.choice(_GIVEN) for _ in range(r.choice([1, 2])))
        city = city or next((c for p, c, _ in _PROVINCES if p == province), "")
        phone = "100" + f"{r.randrange(10**8):08d}"
        user = {
            "user_id": uid, "name": name, "phone": phone, "tier": tier, "points": points,
            "registered_at": self.anchor - timedelta(days=r.randint(200, 900)),
            "advance_refund_suspended_until": advance_suspended_until,
            "address": {"receiver": name, "phone": phone, "province": province, "city": city,
                        "detail": f"{r.choice(_STREETS)}{r.randint(1, 300)}号{r.randint(1, 30)}栋{r.randint(101, 2808)}",
                        "has_elevator": has_elevator, "floor": floor},
            "coupons": [],
        }
        self.users[uid] = user
        return user

    def new_order(self, user: dict, items: list[dict], *, paid_at: datetime | None, created_at: datetime | None = None,
                  coupon_fen: int = 0, points_fen: int = 0, channel: str = "mobile_pay_a",
                  ship: dict | None = None, cancelled_at: datetime | None = None, kind: str = "normal",
                  province: str | None = None, installments: int | None = None) -> dict:
        """items: [{sku_id, qty=1, price?(分), is_gift?, seal_status?, activation_status?, parcel?}]

        ship: None 表示未发货；否则为 {parcels: [{items:[下标], shipped_at, ...轨迹参数}]}，
        省略 parcels 时所有商品同一个包裹。
        """
        oid = "XC" + (paid_at or created_at or self.anchor).strftime("%y%m%d") + self.next_id("O", 6)[1:]
        created_at = created_at or (paid_at - timedelta(minutes=self.rng.randint(1, 20)) if paid_at else self.anchor)
        lines = []
        for idx, it in enumerate(items):
            sku = self.skus.get(it["sku_id"]) if not it.get("is_gift") else None
            price = 0 if it.get("is_gift") else it.get("price", sku["list_price"] if sku else 0)
            lines.append({
                "item_id": f"{oid}-{idx + 1}",
                "sku_id": it["sku_id"] if not it.get("is_gift") else GIFT_SKU["sku_id"],
                "spu_id": sku["spu_id"] if sku else GIFT_SKU["spu_id"],
                "name": sku["name"] if sku else GIFT_SKU["name"],
                "qty": it.get("qty", 1), "unit_price": price, "is_gift": bool(it.get("is_gift")),
                "gift_value": GIFT_SKU["gift_value"] * 100 if it.get("is_gift") else 0,
                "seal_status": it.get("seal_status", "sealed"),
                "activation_status": it.get("activation_status", "n/a"),
            })
        paying = [ln for ln in lines if not ln["is_gift"]]
        allocs = allocate_discount([ln["unit_price"] * ln["qty"] for ln in paying], coupon_fen)
        for ln, a in zip(paying, allocs):
            ln["discount_alloc"] = a
            ln["paid_amount"] = ln["unit_price"] * ln["qty"] - a
        for ln in lines:
            ln.setdefault("discount_alloc", 0)
            ln.setdefault("paid_amount", 0)
        goods_total = sum(ln["unit_price"] * ln["qty"] for ln in paying)
        goods_paid = goods_total - coupon_fen  # 实付商品金额：积分抵扣不从中扣除
        addr = dict(user["address"])
        if province:
            addr["province"] = province
            addr["city"] = next((c for p, c, _ in _PROVINCES if p == province), "")
        fee = self._shipping(lines, addr, paid_at or created_at, user["tier"])
        order = {
            "order_id": oid, "user_id": user["user_id"], "kind": kind, "created_at": created_at, "paid_at": paid_at,
            "cancelled_at": cancelled_at, "items": lines, "goods_total": goods_total, "coupon_discount": coupon_fen,
            "goods_paid": goods_paid, "points_deduction": points_fen,
            "shipping_fee": fee.shipping_fee_fen, "bulky_fee": fee.bulky_fee_fen, "cold_chain_fee": fee.cold_chain_fee_fen,
            "floor_fee": fee.floor_fee_fen, "shipping_rule_version": fee.version,
            "total_paid": goods_paid - points_fen + fee.total_fen if paid_at else 0,
            "payment_channel": channel if paid_at else None, "installments": installments,
            "tier_at_order": user["tier"], "address": addr, "address_change_count": 0, "parcel_ids": [],
            "user_confirmed_at": None,
        }
        self.orders[oid] = order
        if ship and paid_at:
            groups = ship.get("parcels") or [{"items": list(range(len(lines)))}]
            for g in groups:
                params = {k: v for k, v in ship.items() if k != "parcels"} | g
                self._new_parcel(order, [lines[i]["item_id"] for i in g["items"]], **params)
        return order

    def _shipping(self, lines, addr, paid_at, tier):
        groups: dict[str, list] = {"normal": [], "bulky": [], "fresh": []}
        for ln in lines:
            if ln["is_gift"]:
                continue
            line_kind = self.spus[ln["spu_id"]]["line"]
            groups[line_kind].append(ln)
        ship_lines = []
        if groups["normal"]:
            ship_lines.append(ShipLine("normal", sum(x["paid_amount"] for x in groups["normal"])))
        if groups["bulky"]:
            ship_lines.append(ShipLine("bulky", 0, sum(x["qty"] for x in groups["bulky"])))
        if groups["fresh"]:
            ship_lines.append(ShipLine("fresh", sum(x["paid_amount"] for x in groups["fresh"])))
        return calc_shipping(ship_lines, province=addr["province"], paid_at=paid_at, tier=tier,
                             floor=addr.get("floor"), has_elevator=addr.get("has_elevator", True))

    def _new_parcel(self, order: dict, item_ids: list[str], *, shipped_at: datetime, outcome: str = "delivered",
                    signed_at: datetime | None = None, mode: str = "hand", locker_in_at: datetime | None = None,
                    stalled_since: datetime | None = None, lost_at: datetime | None = None,
                    returned_at: datetime | None = None, signer: str | None = None, dirty: bool = False,
                    items: list | None = None) -> dict:
        real = [ln for ln in order["items"] if ln["item_id"] in item_ids and not ln["is_gift"]]
        line_kind = self.spus[real[0]["spu_id"]]["line"] if real else "normal"
        carrier, carrier_name = CARRIERS[line_kind]
        mail_no = carrier + self.next_id("M", 10)[1:]
        city = order["address"]["city"]
        ev = [(shipped_at, "10", f"【星川华东仓】快件已由{carrier_name}揽收")]
        t = shipped_at + timedelta(hours=3)
        ev.append((t, "20", "快件已发往转运中心"))
        if outcome == "stalled":
            ev.append((stalled_since, "20", "快件到达【中转场】，正在分拣"))
        elif outcome == "lost":
            ev.append((t + timedelta(days=1), "20", "快件到达【中转场】，正在分拣"))
            ev.append((lost_at, "50", "快件异常：包裹遗失，承运商已确认"))
        elif outcome == "address_error":
            ev.append((t + timedelta(days=1), "30", f"快件到达【{city}】，派送员正在派件"))
            ev.append((t + timedelta(days=1, hours=3), "50", "派送异常：收件地址错误，无法联系收件人"))
            ev.append((returned_at, "60", "快件退回中"))
        else:
            arrive = (locker_in_at or signed_at) - timedelta(hours=4)
            if arrive > t:
                ev.append((max(t + timedelta(hours=1), arrive - timedelta(days=1)), "20", f"快件到达【{city}转运中心】"))
            ev.append((arrive, "30", f"快件到达【{city}】，派送员正在派件"))
            if mode in ("locker", "station"):
                where = "智能快递柜" if mode == "locker" else "社区驿站"
                ev.append((locker_in_at, "35", f"快件已放入【{where}】，请凭取件码取件"))
            who = signer or {"hand": "本人", "proxy": "门卫", "locker": "快递柜取件", "station": "驿站取件"}[mode]
            ev.append((signed_at, "40", f"已签收，签收人：{who}"))
        traces = [{"time": a, "code": c, "desc": d} for a, c, d in ev]
        if dirty:  # 真实接口常见的重复扫描
            traces.insert(2, dict(traces[1]))
        parcel = {"mail_no": mail_no, "carrier_code": carrier, "carrier_name": carrier_name, "order_id": order["order_id"],
                  "item_ids": item_ids, "mode": mode, "traces": traces, "outcome": outcome}
        self.parcels[mail_no] = parcel
        order["parcel_ids"].append(mail_no)
        return parcel

    # ------------------------------------------------------------ 推导
    def visible_traces(self, parcel: dict) -> list[dict]:
        now = self.clock.now
        return [t for t in parcel["traces"] if t["time"] <= now]

    def parcel_state(self, parcel: dict) -> dict:
        vis = self.visible_traces(parcel)
        codes = [t["code"] for t in vis]
        status = "created"
        if vis:
            last = max(vis, key=lambda t: t["time"])["code"]
            status = {"10": "collected", "20": "in_transit", "30": "out_for_delivery", "35": "in_locker",
                      "40": "delivered", "50": "exception", "60": "returning", "61": "returned"}[last]
        sign = next((t["time"] for t in vis if t["code"] == "40"), None)
        locker = next((t["time"] for t in vis if t["code"] == "35"), None)
        business = None
        if locker or sign:
            business = business_signed_at(sign or locker, parcel["mode"], locker)
        return {"status": status, "signed_at": sign, "locker_in_at": locker, "business_signed_at": business,
                "lost": "50" in codes and parcel["outcome"] == "lost"}

    def item_signed_at(self, order: dict, item_id: str) -> datetime | None:
        for mail_no in order["parcel_ids"]:
            p = self.parcels[mail_no]
            if item_id in p["item_ids"]:
                return self.parcel_state(p)["business_signed_at"]
        return None

    def order_status(self, order: dict) -> str:
        now = self.clock.now
        if order["cancelled_at"] and order["cancelled_at"] <= now:
            return "cancelled"
        if not order["paid_at"] or order["paid_at"] > now:
            if order["created_at"] + timedelta(minutes=30) <= now:
                return "cancelled"
            return "pending_payment"
        if not order["parcel_ids"]:
            return "paid"
        states = [self.parcel_state(self.parcels[m]) for m in order["parcel_ids"]]
        if all(s["status"] == "created" for s in states):
            return "paid"
        signed = [s["business_signed_at"] for s in states if s["business_signed_at"]]
        if len(signed) == len(states):
            last = max(signed)
            if order["user_confirmed_at"] or last + timedelta(days=7) <= now:
                return "completed"
            return "delivered"
        return "partially_delivered" if signed else "shipped"

    # ------------------------------------------------------------ 批量背景数据
    def _background(self, n_users: int, n_orders: int) -> None:
        r = random.Random(self.seed * 7919 + 1)
        self.rng = r
        weights = [w for *_, w in _PROVINCES]
        users = []
        for _ in range(n_users):
            prov = r.choices(_PROVINCES, weights)[0][0]
            tier = r.choices(["normal", "silver", "gold"], [82, 14, 4])[0]
            users.append(self.new_user(tier=tier, province=prov, points=r.randint(0, 6000)))
        sku_ids = [s for s in self.skus if not (self.spus[self.skus[s]["spu_id"]]["line"] == "fresh")]
        fresh_ids = [s for s in self.skus if self.spus[self.skus[s]["spu_id"]]["line"] == "fresh"]
        start = self.anchor - timedelta(days=548)
        span = (self.anchor - start).total_seconds()
        for _ in range(n_orders):
            u = r.choice(users)
            # 时间：大促（6 月、11 月上旬）加权
            while True:
                t = start + timedelta(seconds=r.random() * span)
                promo = (t.month == 6 and t.day <= 18) or (t.month == 11 and t.day <= 11)
                if r.random() < (1.0 if promo else 0.35):
                    break
            hour = r.choices(range(24), [1, 1, 1, 1, 1, 1, 2, 4, 6, 8, 10, 10, 8, 7, 9, 8, 7, 7, 8, 10, 13, 14, 12, 6])[0]
            paid = t.replace(hour=hour, minute=r.randint(0, 59), second=r.randint(0, 59))
            if paid >= self.anchor:
                paid = self.anchor - timedelta(hours=r.randint(1, 72))
            remote = is_remote_province(u["address"]["province"])
            pool = sku_ids if (remote or r.random() > 0.08) else fresh_ids
            picks = r.sample(pool, k=min(len(pool), r.choice([1, 1, 1, 2, 2, 3])))
            items = [{"sku_id": s, "qty": r.choice([1, 1, 1, 2])} for s in picks]
            total = sum(self.skus[s["sku_id"]]["list_price"] * s["qty"] for s in items)
            coupon = r.choice([0, 0, 0, 1000, 2000, 5000]) if total > 10000 else 0
            if r.random() < 0.06:
                items.append({"sku_id": GIFT_SKU["sku_id"], "is_gift": True})
            roll = r.random()
            unpaid = roll < 0.07
            cancelled = None if unpaid else (paid + timedelta(minutes=r.randint(5, 600)) if roll < 0.11 else None)
            age_h = (self.anchor - paid).total_seconds() / 3600
            ship = None
            if not unpaid and not cancelled:
                shipped = paid + timedelta(hours=r.uniform(2, 30))
                transit = timedelta(days=r.uniform(1.2, 4.5) + (3 if remote else 0))
                sign = shipped + transit
                mode = r.choices(["hand", "locker", "station", "proxy"], [65, 18, 10, 7])[0]
                exc = r.random()
                params: dict = {"shipped_at": shipped, "dirty": r.random() < 0.02}
                if exc < 0.012 and age_h < 24 * 20:
                    params |= {"outcome": "stalled", "stalled_since": shipped + timedelta(hours=8)}
                elif exc < 0.014:
                    params |= {"outcome": "lost", "lost_at": shipped + timedelta(days=5)}
                elif exc < 0.019:
                    params |= {"outcome": "address_error", "returned_at": shipped + timedelta(days=3)}
                else:
                    locker_in = sign - timedelta(hours=r.uniform(6, 50)) if mode in ("locker", "station") else None
                    params |= {"mode": mode, "signed_at": sign, "locker_in_at": locker_in}
                if len(items) > 1 and r.random() < 0.22 and params.get("outcome", "delivered") == "delivered":
                    first, rest = [0], list(range(1, len(items)))
                    late = dict(params, signed_at=sign + timedelta(days=1),
                                locker_in_at=params["locker_in_at"] + timedelta(days=1) if params["locker_in_at"] else None)
                    params = {"parcels": [dict(params, items=first), dict(late, items=rest)]}
                ship = params
            channel = r.choices(["mobile_pay_a", "mobile_pay_b", "credit_card", "debit_card", "balance"],
                                [45, 33, 12, 6, 4])[0]
            self.new_order(u, items, paid_at=None if unpaid else paid, created_at=paid - timedelta(minutes=2),
                           coupon_fen=coupon, channel=channel, ship=ship, cancelled_at=cancelled,
                           installments=r.choice([3, 6, 12]) if channel == "credit_card" and r.random() < 0.3 else None)


_WORLD: World | None = None
_WALL_T0 = _walltime.monotonic()


def get_world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World.build()
    return _WORLD


def reset_world(seed: int = 42, anchor: datetime = DEFAULT_ANCHOR) -> World:
    global _WORLD
    _WORLD = World.build(seed=seed, anchor=anchor)
    return _WORLD
