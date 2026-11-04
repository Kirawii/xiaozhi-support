"""运费与优惠分摊。期望值手算，单位：分。"""
import random

import pytest

from xcsim.policy import (
    FreshNotDeliverable,
    ShipLine,
    address_change_fee_delta_fen,
    allocate_discount,
    bj,
    calc_shipping,
    floor_fee_fen,
    is_remote_province,
    item_refund_fen,
    shipping_version,
)

PAID = bj(2026, 10, 20, 12)  # 现行版


def normal(amount):
    return [ShipLine("normal", amount)]


def fee(lines, province="广东", paid_at=PAID, **kw):
    return calc_shipping(lines, province=province, paid_at=paid_at, **kw)


# ------------------------------------------------------------ 普通运费（SHP-01~06）
@pytest.mark.parametrize(
    "amount, expected",
    [(9900, 0), (9899, 800), (11900 - 2000, 0), (0, 800)],  # SHP-01 / 02 / 03 券后 99
)
def test_standard_threshold_is_inclusive_of_99(amount, expected):
    assert fee(normal(amount)).shipping_fee_fen == expected


def test_shp04_points_deduction_does_not_reduce_basis():
    # 实付 94 元 + 积分抵 5 元：调用方把二者相加作为判断金额
    assert fee(normal(9400 + 500)).total_fen == 0


@pytest.mark.parametrize(
    "province, amount, expected",
    [
        ("新疆", 19900, 0),  # SHP-05
        ("青海", 19899, 2000),  # SHP-06
        ("西藏", 19899, 2000),
        ("新疆维吾尔自治区", 19900, 0),
        ("新疆维吾尔自治区", 9900, 2000),  # 偏远不适用 99 门槛
    ],
)
def test_remote_standard(province, amount, expected):
    assert fee(normal(amount), province=province).shipping_fee_fen == expected


def test_is_remote_province():
    assert is_remote_province("青海省") and is_remote_province("西藏自治区")
    assert not is_remote_province("广东") and not is_remote_province("四川")


# ------------------------------------------------------------ 版本（VER-03 / C1）
def test_shipping_version_by_paid_time():
    assert shipping_version(bj(2026, 9, 30, 23, 59, 59)) == "legacy"
    assert shipping_version(bj(2026, 10, 1, 0, 0, 0)) == "current"


def test_ver03_paid_0929_85_yuan_free_under_legacy():
    r = fee(normal(8500), paid_at=bj(2026, 9, 29, 10))
    assert r.shipping_fee_fen == 0 and r.version == "legacy"
    # 同样金额如果是 10-01 之后付款，就要收 8 元
    assert fee(normal(8500), paid_at=bj(2026, 10, 1, 0, 0, 1)).shipping_fee_fen == 800


@pytest.mark.parametrize("amount, expected", [(7900, 0), (7899, 600)])
def test_legacy_79_and_6(amount, expected):
    assert fee(normal(amount), paid_at=bj(2026, 9, 1)).shipping_fee_fen == expected


def test_legacy_standard_rule_covers_remote_too():
    r = fee(normal(7899), province="新疆", paid_at=bj(2026, 9, 1))
    assert r.shipping_fee_fen == 600  # 现行偏远会收 20
    assert fee(normal(7900), province="新疆", paid_at=bj(2026, 9, 1)).shipping_fee_fen == 0


def test_legacy_orders_keep_current_bulky_and_cold_chain():
    lines = [ShipLine("bulky", 0, 1), ShipLine("fresh", 15000)]
    r = fee(lines, paid_at=bj(2026, 9, 1))
    assert r.bulky_fee_fen == 5000 and r.cold_chain_fee_fen == 1500


# ------------------------------------------------------------ 大件、会员（SHP-07）
def test_bulky_per_item_normal_and_remote():
    assert fee([ShipLine("bulky", 0, 1)]).bulky_fee_fen == 5000
    assert fee([ShipLine("bulky", 0, 3)]).bulky_fee_fen == 15000
    assert fee([ShipLine("bulky", 0, 2)], province="西藏").bulky_fee_fen == 24000


def test_shp07_tibet_two_bulky_gold_is_120_yuan():
    r = fee([ShipLine("bulky", 0, 2)], province="西藏", tier="gold")
    assert r.bulky_fee_fen == 12000


def test_gold_bulky_half_off_and_silver_not():
    assert fee([ShipLine("bulky", 0, 1)], tier="gold").bulky_fee_fen == 2500
    assert fee([ShipLine("bulky", 0, 1)], tier="silver").bulky_fee_fen == 5000


def test_gold_standard_free_except_remote():
    assert fee(normal(5000), tier="gold").shipping_fee_fen == 0
    assert fee(normal(5000), province="青海", tier="gold").shipping_fee_fen == 2000
    assert fee(normal(5000), tier="silver").shipping_fee_fen == 800


def test_free_ship_coupon_waives_standard_only():
    r = fee([ShipLine("normal", 5000), ShipLine("bulky", 0, 1)], free_ship_coupon=True)
    assert r.shipping_fee_fen == 0 and r.bulky_fee_fen == 5000


# ------------------------------------------------------------ 冷链、混合（SHP-08）
def test_cold_chain_threshold():
    assert fee([ShipLine("fresh", 19899)]).cold_chain_fee_fen == 1500
    assert fee([ShipLine("fresh", 19900)]).cold_chain_fee_fen == 0  # FRS-03 免冷链费


def test_cold_chain_has_no_member_discount():
    assert fee([ShipLine("fresh", 15000)], tier="gold").cold_chain_fee_fen == 1500


def test_fresh_cannot_go_to_remote():
    with pytest.raises(FreshNotDeliverable):
        fee([ShipLine("fresh", 15000)], province="青海")


def test_shp08_mixed_order_each_group_charged_separately():
    lines = [ShipLine("fresh", 19800), ShipLine("normal", 8000), ShipLine("bulky", 0, 1)]
    r = fee(lines)
    assert (r.cold_chain_fee_fen, r.shipping_fee_fen, r.bulky_fee_fen) == (1500, 800, 5000)
    assert r.total_fen == 7300


def test_group_without_lines_costs_nothing():
    r = fee([ShipLine("bulky", 0, 1)])
    assert r.shipping_fee_fen == 0 and r.cold_chain_fee_fen == 0


def test_normal_lines_are_summed_before_threshold():
    assert fee([ShipLine("normal", 5000), ShipLine("normal", 4900)]).shipping_fee_fen == 0
    assert fee([ShipLine("normal", 5000), ShipLine("normal", 4899)]).shipping_fee_fen == 800


# ------------------------------------------------------------ 楼层费（SHP-09）
@pytest.mark.parametrize(
    "floor, elevator, expected",
    [(8, False, 3000), (5, False, 0), (6, False, 1000), (1, False, 0), (30, True, 0), (None, False, 0), (8, True, 0)],
)
def test_floor_fee(floor, elevator, expected):
    assert floor_fee_fen(floor, elevator) == expected


def test_shp09_mattress_8th_floor_no_elevator():
    r = fee([ShipLine("bulky", 0, 1)], floor=8, has_elevator=False)
    assert r.floor_fee_fen == 3000 and r.total_fen == 8000


def test_floor_fee_only_for_bulky_and_once_per_order():
    assert fee(normal(20000), floor=8, has_elevator=False).floor_fee_fen == 0
    assert fee([ShipLine("bulky", 0, 2)], floor=8, has_elevator=False).floor_fee_fen == 3000


# ------------------------------------------------------------ 改地址（SHP-10）
def test_shp10_change_to_xinjiang_costs_20_more():
    d = address_change_fee_delta_fen(normal(15000), old_province="广东", new_province="新疆", paid_at=PAID)
    assert d == 2000


def test_address_change_back_to_non_remote_refunds_difference():
    d = address_change_fee_delta_fen(normal(15000), old_province="新疆", new_province="广东", paid_at=PAID)
    assert d == -2000


# ------------------------------------------------------------ 优惠分摊（RTA）
def test_rta01_full_reduction_split_by_price_share():
    alloc = allocate_discount([42000, 18000], 6000)
    assert alloc == [4200, 1800]
    assert item_refund_fen(42000, alloc[0]) == 37800  # 378.00


def test_rta02_remainder_goes_to_last_row():
    alloc = allocate_discount([10000, 10000, 10000], 1000)
    assert alloc == [333, 333, 334]  # 3.33 / 3.33 / 3.34
    assert item_refund_fen(10000, alloc[0]) == 9667  # 96.67
    assert sum(alloc[1:]) == 667 and item_refund_fen(10000, alloc[1]) + item_refund_fen(10000, alloc[2]) == 19333


def test_allocation_half_cent_rounds_up():
    assert allocate_discount([1, 1], 1) == [1, 0]  # 0.5 -> 1，尾差进最后一行
    assert allocate_discount([1, 1, 1], 1) == [0, 0, 1]


def test_allocation_edge_cases():
    assert allocate_discount([], 100) == []
    assert allocate_discount([500], 120) == [120]
    assert allocate_discount([500, 300], 0) == [0, 0]
    assert allocate_discount([0, 0], 10) == [0, 0]
    assert allocate_discount([30000, 10000], 4000) == [3000, 1000]


def test_allocation_sums_to_total_and_stays_close_to_exact_share():
    rng = random.Random(20261101)
    for _ in range(300):
        amounts = [rng.randint(1, 500000) for _ in range(rng.randint(1, 6))]
        total_discount = rng.randint(0, sum(amounts))
        shares = allocate_discount(amounts, total_discount)
        assert sum(shares) == total_discount
        for a, s in zip(amounts[:-1], shares[:-1]):
            exact = total_discount * a / sum(amounts)
            assert abs(s - exact) <= 0.5 + 1e-9
