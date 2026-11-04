"""无理由退货窗口、质量档位、错发漏发、生鲜。"""
import pytest

from xcsim.policy import (
    bj,
    business_signed_at,
    can_apply_again,
    exchange_block_rule,
    fresh_refund,
    is_return_window_open,
    needs_inspection_report,
    pickup_fee_fen,
    quality_options,
    quality_tier,
    remaining_applications,
    return_block_rule,
    return_insurance_payout_fen,
    return_window,
    within_fresh_claim,
    within_wrong_or_missing,
)

N = bj(2026, 11, 1, 10)


# ------------------------------------------------------------ 窗口（RET / VER）
def test_ret01_day7_2330_open_until_end_of_day():
    w = return_window(bj(2026, 10, 25, 14, 10))
    assert w.version == "current" and w.window_days == 7
    assert w.deadline_at == bj(2026, 11, 1, 23, 59, 59)
    assert is_return_window_open(w, bj(2026, 11, 1, 23, 30))
    assert is_return_window_open(w, bj(2026, 11, 1, 23, 59, 59))


def test_ret02_closed_after_midnight():
    w = return_window(bj(2026, 10, 25, 14, 10))
    assert not is_return_window_open(w, bj(2026, 11, 2, 0, 0, 0))
    assert not is_return_window_open(w, bj(2026, 11, 2, 0, 30))


def test_ret03_over_168_hours_but_still_day_7():
    signed = bj(2026, 10, 25, 0, 5)
    now = bj(2026, 11, 1, 23, 0)
    assert (now - signed).total_seconds() / 3600 > 168  # 190 小时多
    assert is_return_window_open(return_window(signed), now)


def test_ret04_signing_day_not_counted():
    w = return_window(bj(2026, 10, 24, 23, 50))
    assert w.deadline_at == bj(2026, 10, 31, 23, 59, 59)
    assert not is_return_window_open(w, N)


def test_ret05_station_in_time_is_the_basis():
    signed = business_signed_at(bj(2026, 10, 26, 9, 0), "station", bj(2026, 10, 23, 18, 0))
    w = return_window(signed)
    assert w.deadline_at == bj(2026, 10, 30, 23, 59, 59)
    assert not is_return_window_open(w, N)


def test_ret06_locker_in_then_pickup_next_day_still_ok_today():
    signed = business_signed_at(bj(2026, 10, 27, 8, 0), "locker", bj(2026, 10, 25, 22, 10))
    w = return_window(signed)
    assert w.deadline_at == bj(2026, 11, 1, 23, 59, 59)
    assert is_return_window_open(w, N)


def test_ver01_legacy_15_days():
    w = return_window(bj(2026, 9, 28, 15, 0))
    assert (w.version, w.window_days, w.policy_key) == ("legacy", 15, "legacy_return.window_days")
    assert w.deadline_at == bj(2026, 10, 13, 23, 59, 59)
    assert is_return_window_open(w, bj(2026, 10, 10, 10))
    assert not is_return_window_open(w, bj(2026, 10, 14, 0, 0))


def test_ver02_first_second_after_cutover_is_current():
    assert return_window(bj(2026, 10, 1, 0, 0, 30)).window_days == 7
    assert return_window(bj(2026, 10, 1, 0, 0, 0)).version == "current"
    w = return_window(bj(2026, 10, 1, 0, 0, 30))
    assert not is_return_window_open(w, bj(2026, 10, 12, 10))  # 截止 10-08


def test_cutover_boundary_last_second_is_legacy():
    assert return_window(bj(2026, 9, 30, 23, 59, 59)).version == "legacy"


def test_ver04_signed_0930_already_expired_even_with_15_days():
    w = return_window(bj(2026, 9, 30, 20, 0))
    assert w.deadline_at == bj(2026, 10, 15, 23, 59, 59)
    assert not is_return_window_open(w, N)


# ------------------------------------------------------------ 次数（RET-07/08）
def test_application_limit():
    assert can_apply_again(0) and can_apply_again(1)
    assert not can_apply_again(2) and not can_apply_again(3)
    assert remaining_applications(1) == 1  # RET-07：告知这是最后一次
    assert remaining_applications(2) == 0
    assert remaining_applications(5) == 0


# ------------------------------------------------------------ 不可退品类
@pytest.mark.parametrize("tag", ["fresh", "custom", "digital"])
def test_block_by_category(tag):
    assert return_block_rule(tags={tag}) == "AS_NOT_RETURNABLE_CATEGORY"


def test_block_intimate_only_when_opened():
    assert return_block_rule(tags={"intimate"}, opened=True) == "AS_OPENED"  # RET-09
    assert return_block_rule(tags={"intimate"}, opened=False) is None  # RET-10


@pytest.mark.parametrize("kind", ["phone", "tablet", "laptop", "smartwatch"])
def test_block_activated_electronics(kind):
    assert return_block_rule(tags=set(), kind=kind, activated=True) == "AS_ACTIVATED"  # RET-11
    assert return_block_rule(tags=set(), kind=kind, activated=False) is None


def test_activation_does_not_block_other_kinds():
    assert return_block_rule(tags=set(), kind="speaker", activated=True) is None


def test_block_mattress_only_when_seal_removed():
    assert return_block_rule(tags={"mattress"}, seal_removed=True) == "AS_SEAL_REMOVED"
    assert return_block_rule(tags={"mattress"}, seal_removed=False) is None


def test_ordinary_goods_not_blocked():
    assert return_block_rule(tags={"apparel"}, kind="coat", opened=True, activated=True) is None


# ------------------------------------------------------------ 换货（RTA-06/07）
@pytest.mark.parametrize("cat", ["apparel", "footwear"])
def test_exchange_allowed_first_time(cat):
    assert exchange_block_rule(cat, 0) is None


def test_exchange_second_time_rejected():
    assert exchange_block_rule("apparel", 1) == "AS_EXCHANGE_LIMIT"


def test_exchange_other_category_not_supported():
    assert exchange_block_rule("home", 0) == "AS_EXCHANGE_NOT_SUPPORTED"


# ------------------------------------------------------------ 取件费与运费险
def test_pickup_fee_and_return_insurance():
    assert pickup_fee_fen() == 1200
    assert 19900 - pickup_fee_fen() == 18700  # RTA-04
    assert return_insurance_payout_fen(1800) == 1500  # RTA-05
    assert return_insurance_payout_fen(1500) == 1500
    assert return_insurance_payout_fen(1501) == 1500
    assert return_insurance_payout_fen(999) == 999
    assert return_insurance_payout_fen(0) == 0


# ------------------------------------------------------------ 质量档位（QLT）
@pytest.mark.parametrize(
    "k, options",
    [
        (1, ("refund", "exchange", "repair")),
        (7, ("refund", "exchange", "repair")),
        (8, ("exchange", "repair")),
        (15, ("exchange", "repair")),
        (16, ("warranty",)),
        (400, ("warranty",)),
    ],
)
def test_quality_tier_options(k, options):
    assert tuple(quality_tier(k).options) == options


def test_quality_tier_day_zero_uses_first_tier():
    assert "refund" in quality_tier(0).options


@pytest.mark.parametrize(
    "signed, options",
    [
        (bj(2026, 10, 25, 9), ("refund", "exchange", "repair")),  # D7 QLT-01
        (bj(2026, 10, 24, 9), ("exchange", "repair")),  # D8 QLT-02
        (bj(2026, 10, 17, 9), ("exchange", "repair")),  # D15 QLT-04
        (bj(2026, 10, 16, 9), ("warranty",)),  # D16 QLT-05
    ],
)
def test_quality_options_by_date(signed, options):
    assert quality_options(signed, N) == options


def test_qlt03_exchange_out_of_stock_allows_refund_in_second_tier():
    signed = bj(2026, 10, 20, 9)  # D12
    assert quality_options(signed, N, exchange_out_of_stock=True) == ("refund", "exchange", "repair")
    assert quality_options(signed, N, exchange_out_of_stock=False) == ("exchange", "repair")


def test_out_of_stock_does_not_reopen_refund_after_day_15():
    assert quality_options(bj(2026, 10, 16, 9), N, exchange_out_of_stock=True) == ("warranty",)


# ------------------------------------------------------------ 错发漏发 48 小时（QLT-06/07/10）
def test_wrong_or_missing_48h():
    from datetime import timedelta

    assert within_wrong_or_missing(N - timedelta(hours=47), N)
    assert within_wrong_or_missing(N - timedelta(hours=48), N)  # 含第 48 小时整
    assert not within_wrong_or_missing(N - timedelta(hours=48, seconds=1), N)
    assert not within_wrong_or_missing(N - timedelta(hours=49), N)
    assert within_wrong_or_missing(N - timedelta(hours=20), N)


# ------------------------------------------------------------ 生鲜（FRS）
def test_fresh_claim_window_24h():
    from datetime import timedelta

    assert not within_fresh_claim(N - timedelta(hours=25), N)  # FRS-01
    assert within_fresh_claim(N - timedelta(hours=23), N)  # FRS-02
    assert within_fresh_claim(N - timedelta(hours=24), N)
    assert not within_fresh_claim(N - timedelta(hours=24, minutes=1), N)


def test_frs02_ratio_20_percent():
    r = fresh_refund(18000, 1500, "0.20")
    assert r.refund_fen == 3600 and not r.full_refund and not r.cold_chain_refunded


def test_frs03_exactly_30_percent_is_still_partial():
    r = fresh_refund(19900, 0, "0.30")
    assert r.refund_fen == 5970 and not r.full_refund


def test_frs04_over_30_percent_full_refund_including_cold_chain():
    r = fresh_refund(15000, 1500, "0.31")
    assert r.refund_fen == 16500 and r.full_refund and r.cold_chain_refunded


def test_fresh_ratio_just_above_threshold_is_full():
    assert fresh_refund(10000, 0, "0.3001").full_refund


def test_fresh_partial_rounds_half_up_to_fen():
    assert fresh_refund(333, 0, "0.15").refund_fen == 50  # 49.95 -> 50
    assert fresh_refund(1, 0, "0.5").refund_fen == 1  # 0.5 -> 1
    assert fresh_refund(10, 0, "0.25").refund_fen == 3  # 2.5 -> 3


# ------------------------------------------------------------ 检测报告（QLT-08/09）
def test_inspection_report_digital_price_threshold_is_exclusive():
    kw = dict(kind="phone", is_digital_category=True)
    assert needs_inspection_report(unit_price_fen=329900, **kw)  # QLT-08
    assert not needs_inspection_report(unit_price_fen=300000, **kw)  # QLT-09 恰好 3000.00
    assert needs_inspection_report(unit_price_fen=300001, **kw)
    assert not needs_inspection_report(unit_price_fen=299999, **kw)


@pytest.mark.parametrize("kind", ["fridge", "washer", "air_conditioner", "tv"])
def test_inspection_report_for_appliances_any_price(kind):
    assert needs_inspection_report(kind=kind, unit_price_fen=100, is_digital_category=False)


def test_inspection_report_not_for_expensive_non_digital():
    assert not needs_inspection_report(kind="sofa", unit_price_fen=900000, is_digital_category=False)
