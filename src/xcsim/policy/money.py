"""整数分运算工具。金额一律是 int（分），中间值用 Decimal，不用 float。"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def mul_ratio(amount_fen: int, ratio: Decimal | str | int) -> int:
    """分 × 比例，四舍五入（半入）到分。"""
    value = Decimal(amount_fen) * Decimal(str(ratio))
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def div_round(numerator: int, denominator: int) -> int:
    """整数除法，四舍五入（半入），只用于非负数。"""
    return (2 * numerator + denominator) // (2 * denominator)


def floor_ratio(amount: int, ratio: Decimal | str | int) -> int:
    """向下取整的 amount × ratio（用于积分等不能出现小数的量）。"""
    return int((Decimal(amount) * Decimal(str(ratio))).to_integral_value(rounding="ROUND_FLOOR"))
