"""运费：普通、偏远、大件、冷链、混合订单、楼层费、会员权益、新旧版本。"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .loader import get_policy
from .models import Policy, fen
from .money import mul_ratio
from .timeutil import to_bj

LineKind = Literal["normal", "bulky", "fresh"]


class FreshNotDeliverable(ValueError):
    """生鲜不配送偏远地区（对应订单服务 40906）。"""


@dataclass(frozen=True)
class ShipLine:
    """一组同类商品。

    amount_fen 是判断“满额包邮”用的金额：实付商品款 + 积分抵扣（积分不扣减，SHP-04），
    不含运费。bulky 行只看 qty（件数）。
    """

    kind: LineKind
    amount_fen: int = 0
    qty: int = 1


@dataclass(frozen=True)
class ShippingFees:
    shipping_fee_fen: int  # 普通商品运费（含偏远）
    bulky_fee_fen: int
    cold_chain_fee_fen: int
    floor_fee_fen: int
    version: str  # legacy / current

    @property
    def total_fen(self) -> int:
        return self.shipping_fee_fen + self.bulky_fee_fen + self.cold_chain_fee_fen + self.floor_fee_fen


def shipping_version(paid_at: datetime, policy: Policy | None = None) -> Literal["legacy", "current"]:
    """运费版本按付款时间判定（C1/F1）：付款早于 cutover 日 00:00 用旧版。"""
    p = policy or get_policy()
    return "legacy" if to_bj(paid_at, p).date() < p.meta.cutover_date else "current"


def is_remote_province(province: str, policy: Policy | None = None) -> bool:
    """省份名可带“省/自治区”等后缀，按前缀匹配。"""
    p = policy or get_policy()
    return any(province.startswith(x) for x in p.shipping.current.remote_provinces)


def floor_fee_fen(floor: int | None, has_elevator: bool, policy: Policy | None = None) -> int:
    """楼层费（大件、无电梯）：超过免费楼层的每层收费。"""
    rule = (policy or get_policy()).delivery.floor_fee
    if has_elevator or floor is None or floor <= rule.free_up_to_floor:
        return 0
    return (floor - rule.free_up_to_floor) * fen(rule.per_floor)


def calc_shipping(
    lines: Sequence[ShipLine],
    *,
    province: str,
    paid_at: datetime,
    tier: str = "normal",
    free_ship_coupon: bool = False,
    floor: int | None = None,
    has_elevator: bool = True,
    policy: Policy | None = None,
) -> ShippingFees:
    """混合订单分别计费：普通、大件、生鲜各算各的。

    - 版本按付款时间；旧版 79/6 覆盖全部普通商品含偏远，大件和冷链沿用现行数字（C1）。
    - 金星：普通运费全国免（含偏远，P04）；大件 5 折。银星免邮券（free_ship_coupon）免普通运费。
    - 冷链费不享受会员优惠。楼层费整单只收一次，仅大件。
    """
    p = policy or get_policy()
    cur = p.shipping.current
    version = shipping_version(paid_at, p)
    remote = is_remote_province(province, p)

    normal_amount = sum(x.amount_fen for x in lines if x.kind == "normal")
    has_normal = any(x.kind == "normal" for x in lines)
    fresh_amount = sum(x.amount_fen for x in lines if x.kind == "fresh")
    has_fresh = any(x.kind == "fresh" for x in lines)
    bulky_items = sum(x.qty for x in lines if x.kind == "bulky")

    # 普通商品
    standard_fee = 0
    if has_normal:
        if version == "legacy":
            rule = p.shipping.legacy.standard
        else:
            rule = cur.remote if remote else cur.standard
        if normal_amount < fen(rule.free_over):
            standard_fee = fen(rule.fee)
        gold_free = p.shipping.member.gold_standard_free and tier == "gold"
        if gold_free or (free_ship_coupon and p.coupon_stacking.free_ship_stackable):
            standard_fee = 0

    # 大件
    bulky_fee = 0
    if bulky_items:
        per_item = fen(cur.bulky.remote_fee_per_item if remote else cur.bulky.fee_per_item)
        bulky_fee = per_item * bulky_items
        if tier == "gold":
            bulky_fee = mul_ratio(bulky_fee, p.shipping.member.gold_bulky_discount)

    # 冷链
    cold_fee = 0
    if has_fresh:
        if remote and not cur.fresh.deliver_to_remote:
            raise FreshNotDeliverable(province)
        if fresh_amount < fen(cur.fresh.free_over):
            cold_fee = fen(cur.fresh.cold_chain_fee)

    floor_fee = floor_fee_fen(floor, has_elevator, p) if bulky_items else 0
    return ShippingFees(standard_fee, bulky_fee, cold_fee, floor_fee, version)


def address_change_fee_delta_fen(
    lines: Sequence[ShipLine],
    *,
    old_province: str,
    new_province: str,
    paid_at: datetime,
    tier: str = "normal",
    policy: Policy | None = None,
) -> int:
    """改地址后需要补（正）或退（负）的运费差额，SHP-10。"""
    old = calc_shipping(lines, province=old_province, paid_at=paid_at, tier=tier, policy=policy)
    new = calc_shipping(lines, province=new_province, paid_at=paid_at, tier=tier, policy=policy)
    return new.total_fen - old.total_fen
