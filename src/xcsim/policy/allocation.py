"""优惠分摊：按原价占比，四舍五入到分，尾差进最后一行。"""
from __future__ import annotations

from collections.abc import Sequence

from .money import div_round


def allocate_discount(line_amounts_fen: Sequence[int], total_discount_fen: int) -> list[int]:
    """把总优惠分摊到各行（行金额 = 单价 × 数量，赠品行不要传进来）。

    前面各行 = round(总优惠 × 行金额 / 合计)，最后一行 = 总优惠 - 前面之和，
    所以明细之和永远等于总额。
    """
    if not line_amounts_fen:
        return []
    total = sum(line_amounts_fen)
    if total <= 0 or total_discount_fen <= 0:
        return [0] * len(line_amounts_fen)
    shares: list[int] = []
    for amount in line_amounts_fen[:-1]:
        shares.append(div_round(total_discount_fen * amount, total))
    shares.append(total_discount_fen - sum(shares))
    return shares


def item_refund_fen(line_amount_fen: int, discount_alloc_fen: int) -> int:
    """退某一行时的应退金额 = 行原价 - 该行分摊到的优惠。"""
    return line_amount_fen - discount_alloc_fen
