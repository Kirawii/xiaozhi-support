"""退款门槛、到账时间、价保、保修、发票、会员与积分。"""
from datetime import date, timedelta

import pytest

from xcsim.policy import (
    advance_refund_decision,
    arrival_eta,
    bj,
    can_issue_invoice,
    clawback_points,
    completion_time,
    earn_points,
    in_warranty,
    invoice_deadline,
    is_birthday_month,
    clawback_shortfall_deduction_fen,
    repair_part_warranty_end,
    manual_review_due_at,
    max_redeemable_points,
    needs_manual_review,
    next_assessment_at,
    paid_repair_warranty_end,
    part_warranty_end,
    points_available_at,
    points_expiry,
    points_multiplier,
    points_refund_amount,
    points_to_fen,
    price_protection,
    price_protection_window_days,
    replacement_due,
    suspension_until,
    tier_for_spend,
    tier_window,
    warranty_end,
    warranty_months,
)

N = bj(2026, 11, 1, 10)  # 周日


# ------------------------------------------------------------ 人工复核（RFD-01~03）
@pytest.mark.parametrize("gross, expected", [(200000, False), (200001, True), (199999, False), (0, False)])
def test_manual_review_threshold_exclusive_2000(gross, expected):
    assert needs_manual_review(gross) is expected


def test_rfd03_threshold_uses_gross_before_pickup_fee():
    gross, pickup = 200500, 1200
    assert needs_manual_review(gross)
    assert not needs_manual_review(gross - pickup)  # 若按扣费后判断会漏掉


def test_manual_review_due_next_business_day():
    assert manual_review_due_at(N) == bj(2026, 11, 2, 10)  # 周日 -> 周一
    assert manual_review_due_at(bj(2026, 10, 30, 15)) == bj(2026, 11, 2, 15)  # 周五 -> 周一
    assert manual_review_due_at(bj(2026, 11, 3, 15)) == bj(2026, 11, 4, 15)


# ------------------------------------------------------------ 先行退款（RFD-04~08）
def advance(**kw):
    base = dict(tier="gold", gross_refund_fen=50000, has_valid_return_tracking=True, suspended_until=None, now=N)
    base.update(kw)
    return advance_refund_decision(**base)


def test_rfd04_gold_500_with_tracking_allowed():
    d = advance()
    assert d.allowed and d.reason is None


def test_rfd05_over_500_must_wait_for_inspection():
    assert advance(gross_refund_fen=50001).reason == "amount"
    assert advance(gross_refund_fen=49999).allowed


def test_rfd06_no_tracking_number_not_allowed():
    assert advance(has_valid_return_tracking=False).reason == "no_tracking"


@pytest.mark.parametrize("tier", ["silver", "normal"])
def test_rfd07_only_gold(tier):
    assert advance(tier=tier, gross_refund_fen=30000).reason == "tier"


def test_rfd08_suspended_until_future_blocks_until_boundary():
    until = N + timedelta(days=30)
    assert advance(suspended_until=until).reason == "suspended"
    assert advance(suspended_until=until, now=until - timedelta(seconds=1)).reason == "suspended"
    assert advance(suspended_until=until, now=until).allowed  # 到点即恢复
    assert advance(suspended_until=N - timedelta(days=1)).allowed


def test_suspension_after_failed_inspection_is_90_days():
    assert suspension_until(N) == bj(2027, 1, 30, 10)


# ------------------------------------------------------------ 到账时间（RFD-09）
def test_arrival_eta_by_channel_business_days():
    start = bj(2026, 10, 30, 10)  # 周五
    assert arrival_eta("balance", start) == (start, start)
    assert arrival_eta("mobile_pay_a", start) == (start, bj(2026, 11, 2, 10))
    assert arrival_eta("mobile_pay_b", start) == (start, bj(2026, 11, 2, 10))
    assert arrival_eta("debit_card", start) == (bj(2026, 11, 4, 10), bj(2026, 11, 10, 10))
    assert arrival_eta("credit_card", start) == (bj(2026, 11, 4, 10), bj(2026, 11, 20, 10))


def test_arrival_eta_skips_national_day():
    start = bj(2026, 9, 29, 10)  # 周二，之后 10-01~10-07 放假
    lo, hi = arrival_eta("debit_card", start)
    assert lo == bj(2026, 10, 9, 10)  # 9-30(1) 10-08(2) 10-09(3)
    assert hi == bj(2026, 10, 15, 10)  # 再加 10-12/13/14/15 共 4 个工作日 = 第 7 个


def test_arrival_eta_unknown_channel():
    with pytest.raises(ValueError):
        arrival_eta("cash", N)


def test_points_refund_one_point_per_fen():
    assert points_refund_amount(500) == 500  # 5 元积分抵扣 = 500 积分
    assert points_refund_amount(0) == 0


def test_p03_shortfall_points_deducted_from_refund_at_100_points_per_yuan():
    assert clawback_shortfall_deduction_fen(250) == 250  # 差 250 积分 -> 2.50 元
    assert clawback_shortfall_deduction_fen(100) == 100  # 1 元
    assert clawback_shortfall_deduction_fen(1) == 1
    assert clawback_shortfall_deduction_fen(0) == 0
    assert clawback_shortfall_deduction_fen(-5) == 0


def test_points_functions_follow_their_own_policy_keys(tmp_path, monkeypatch):
    import yaml
    from pathlib import Path

    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "policy" / "policy.yaml").read_text(encoding="utf-8"))
    raw["refund"]["points_clawback_rate"] = 50  # 只改扣回比例
    f = tmp_path / "m.yaml"
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("XC_POLICY_PATH", str(f))
    assert clawback_shortfall_deduction_fen(250) == 500  # 随 points_clawback_rate 变化
    assert points_refund_amount(500) == 500  # 退回积分只看 redeem_rate，不受影响


# ------------------------------------------------------------ 价保（PPR）
def pp(tier="normal", paid=bj(2026, 10, 26, 9), unit=20000, cur=18900, ptype="regular", kind="normal", **kw):
    return price_protection(
        tier=tier, paid_at=paid, now=N, paid_unit_fen=unit, current_price_fen=cur, current_price_type=ptype, order_kind=kind, **kw
    )


def test_window_days_by_tier():
    assert [price_protection_window_days(t) for t in ("normal", "silver", "gold")] == [7, 7, 15]


def test_ppr01_normal_day7_eligible_diff_11_yuan():
    r = pp()
    assert r.eligible and r.day_k == 7 and r.diff_fen == 1100


def test_ppr02_normal_day8_rejected():
    r = pp(paid=bj(2026, 10, 25, 9))
    assert not r.eligible and r.reason == "window" and r.day_k == 8


def test_ppr03_gold_day15_ok_but_normal_and_silver_expired():
    paid = bj(2026, 10, 18, 9)
    assert pp(tier="gold", paid=paid).eligible and pp(tier="gold", paid=paid).day_k == 15
    assert pp(tier="silver", paid=paid).reason == "window"
    assert pp(tier="normal", paid=paid).reason == "window"
    assert pp(tier="gold", paid=bj(2026, 10, 17, 9)).reason == "window"  # 第 16 天


@pytest.mark.parametrize("ptype", ["flash_sale", "group_buy", "live_only", "clearance"])
def test_ppr04_excluded_price_types(ptype):
    assert pp(ptype=ptype).reason == "price_type"


def test_promo_price_type_is_allowed():
    assert pp(ptype="promo").eligible


def test_presale_orders_excluded():
    assert pp(kind="presale").reason == "order_kind"


def test_gift_row_excluded_but_full_price_row_in_same_order_allowed():
    # P08 三.4：赠品不适用；含赠品订单里的正价商品仍可价保
    assert pp(kind="gift_included", is_gift=True).reason == "gift_item"
    r = pp(kind="gift_included", is_gift=False)
    assert r.eligible and r.diff_fen == 1100


def test_item_with_return_application_excluded():
    assert pp(has_return_application=True).reason == "returned"


def test_diff_is_multiplied_by_quantity():
    # P08 二.1：（实付单价 − 申请时售价）× 数量 = (200.00 − 189.00) × 3
    r = pp(qty=3)
    assert r.eligible and r.diff_fen == 3300
    assert pp(qty=2, unit=20000, cur=18900).diff_fen == 2200
    assert pp(qty=5, unit=18900, cur=18900).diff_fen == 0


def test_ppr05_coupon_paid_already_below_current_price():
    r = pp(unit=18000, cur=18900)
    assert not r.eligible and r.reason == "no_diff" and r.diff_fen == 0
    assert pp(unit=18900, cur=18900).reason == "no_diff"
    assert pp(unit=18901, cur=18900).diff_fen == 1


# ------------------------------------------------------------ 保修（WAR，C5）
def test_war01_last_day_in_warranty():
    signed = bj(2025, 11, 2, 15)
    assert warranty_end(signed, 12) == bj(2026, 11, 1, 23, 59, 59)
    assert in_warranty(signed, N, 12)
    assert not in_warranty(signed, bj(2026, 11, 2, 0, 0, 0), 12)


def test_war02_one_day_over_is_paid_repair():
    signed = bj(2025, 11, 1, 15)
    assert warranty_end(signed, 12) == bj(2026, 10, 31, 23, 59, 59)
    assert not in_warranty(signed, N, 12)


def test_war03_refurbished_is_six_months():
    assert warranty_months(refurbished=True, spu_months=24) == 6
    assert warranty_months(refurbished=False, spu_months=24) == 24
    assert warranty_months(refurbished=False) == 12
    signed = bj(2026, 4, 1, 9)  # 约 7 个月前
    assert not in_warranty(signed, N, 6)  # 到 09-30 止
    assert in_warranty(bj(2026, 5, 2, 9), N, 6)  # 到 11-01 止


def test_warranty_end_clamps_for_month_end_signing():
    assert warranty_end(bj(2024, 2, 29, 9), 12) == bj(2025, 2, 27, 23, 59, 59)


def test_part_and_paid_repair_warranty_are_inclusive_of_start_day():
    assert part_warranty_end(bj(2026, 10, 1, 9), 90) == bj(2026, 12, 29, 23, 59, 59)
    assert paid_repair_warranty_end(bj(2026, 11, 1, 9)) == bj(2027, 1, 29, 23, 59, 59)


def test_p06_repaired_part_gets_the_longer_of_remaining_whole_warranty_and_90_days():
    repaired = bj(2026, 11, 1, 10)
    # 整机还剩很久（到 2027-06-30）：比 90 天长，取整机剩余期
    assert repair_part_warranty_end(repaired, bj(2027, 6, 30, 23, 59, 59)) == bj(2027, 6, 30, 23, 59, 59)
    # 整机只剩 14 天（到 2026-11-15）：不足 90 天，取 90 天（含维修当日，到 2027-01-29）
    assert repair_part_warranty_end(repaired, bj(2026, 11, 15, 23, 59, 59)) == bj(2027, 1, 29, 23, 59, 59)
    # 整机今天到期：同样取 90 天
    assert repair_part_warranty_end(repaired, bj(2026, 11, 1, 23, 59, 59)) == bj(2027, 1, 29, 23, 59, 59)


def test_replacement_after_two_failed_repairs():
    assert not replacement_due(0) and not replacement_due(1)
    assert replacement_due(2) and replacement_due(3)


# ------------------------------------------------------------ 发票（INV）
def test_completion_is_seven_days_after_delivery():
    assert completion_time(bj(2026, 10, 25, 14, 10)) == bj(2026, 11, 1, 14, 10)


def test_inv01_day180_ok_inv02_day181_rejected():
    completed = bj(2026, 5, 5, 10)
    assert invoice_deadline(completed) == bj(2026, 11, 1, 23, 59, 59)
    assert can_issue_invoice(completed, N)
    assert can_issue_invoice(completed, bj(2026, 11, 1, 23, 59, 59))
    assert not can_issue_invoice(completed, bj(2026, 11, 2, 0, 0, 0))
    assert not can_issue_invoice(bj(2026, 5, 4, 10), N)  # 第 181 天


# ------------------------------------------------------------ 会员等级（MEM）
@pytest.mark.parametrize(
    "spend, tier",
    [(0, "normal"), (199999, "normal"), (200000, "silver"), (799999, "silver"), (800000, "gold"), (1500000, "gold")],
)
def test_tier_thresholds_inclusive(spend, tier):
    assert tier_for_spend(spend) == tier  # MEM-01 恰好 8000.00 为金星；MEM-02 7999.99 为银星


def test_tier_window_is_previous_12_months_ending_first_of_month():
    expected = (bj(2025, 11, 1), bj(2026, 11, 1))
    assert tier_window(bj(2026, 11, 1, 0, 0)) == expected
    assert tier_window(bj(2026, 11, 20, 15)) == expected
    assert tier_window(bj(2026, 3, 1)) == (bj(2025, 3, 1), bj(2026, 3, 1))


def test_next_assessment_is_strictly_after_now():
    assert next_assessment_at(N) == bj(2026, 12, 1)
    assert next_assessment_at(bj(2026, 10, 31, 23, 59)) == bj(2026, 11, 1)
    assert next_assessment_at(bj(2026, 11, 1, 0, 0, 0)) == bj(2026, 12, 1)
    assert next_assessment_at(bj(2026, 12, 15)) == bj(2027, 1, 1)


# ------------------------------------------------------------ 积分
def test_multipliers():
    assert points_multiplier("normal") == 1 and points_multiplier("silver") == 1
    assert str(points_multiplier("gold")) == "1.5"
    assert points_multiplier("silver", birthday=True) == 2 and points_multiplier("gold", birthday=True) == 2
    assert points_multiplier("normal", birthday=True) == 1  # 普通会员生日无加成


def test_earn_points_floor():
    assert earn_points(25900, "normal") == 259
    assert earn_points(25900, "gold") == 388  # 388.5 向下
    assert earn_points(25900, "gold", birthday=True) == 518
    assert earn_points(99, "normal") == 0
    assert earn_points(100, "normal") == 1


def test_birthday_benefit_applies_for_the_whole_birthday_month():
    # P07：生日当月双倍积分，只看月份
    assert is_birthday_month(date(1990, 11, 1), date(2026, 11, 1))
    assert is_birthday_month(date(1990, 11, 30), date(2026, 11, 1))
    assert is_birthday_month(date(1990, 11, 2), date(2026, 11, 30))
    assert not is_birthday_month(date(1990, 10, 31), date(2026, 11, 1))
    assert not is_birthday_month(date(1990, 12, 1), date(2026, 11, 30))


def test_mem03_points_credited_exactly_seven_days_after_signing():
    signed = N - timedelta(days=7) + timedelta(hours=1)
    assert points_available_at(signed) == N + timedelta(hours=1)


@pytest.mark.parametrize(
    "earned, expiry",
    [
        (bj(2026, 11, 1, 10), bj(2027, 12, 31, 23, 59, 59)),
        (bj(2026, 12, 31, 23, 59), bj(2027, 12, 31, 23, 59, 59)),
        (bj(2027, 1, 1, 0, 0), bj(2028, 12, 31, 23, 59, 59)),
    ],
)
def test_points_expire_end_of_next_year(earned, expiry):
    assert points_expiry(earned) == expiry


def test_redeem_cap_ten_percent_floor():
    assert max_redeemable_points(25900) == 2590
    assert max_redeemable_points(25909) == 2590
    assert max_redeemable_points(9) == 0
    assert max_redeemable_points(10) == 1
    assert points_to_fen(500) == 500  # 100 积分 = 1 元


def test_clawback_proportional_rounded_and_capped():
    assert clawback_points(259, 25900, 12950) == 130  # 129.5 -> 130
    assert clawback_points(259, 25900, 25900) == 259
    assert clawback_points(259, 25900, 99999) == 259
    assert clawback_points(259, 25900, 0) == 0
    assert clawback_points(0, 25900, 100) == 0
    assert clawback_points(10, 0, 100) == 0
