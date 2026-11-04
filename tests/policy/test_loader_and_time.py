"""policy 加载与时间工具。期望值全部手算（日历见注释）。"""
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from xcsim.policy import (
    add_business_days,
    bj,
    business_signed_at,
    day_k_after,
    day_k_from_payment,
    get_policy,
    in_service_hours,
    is_business_day,
    load_policy,
    to_bj,
)
from xcsim.policy.timeutil import add_months, within_hours

ROOT = Path(__file__).resolve().parents[2]


def test_policy_yaml_is_verbatim_copy_of_design_doc():
    text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    block = re.search(r"### 1\.1 完整内容.*?```yaml\n(.*?)\n```", text, re.S).group(1)
    assert (ROOT / "policy" / "policy.yaml").read_text(encoding="utf-8").rstrip("\n") == block


def test_policy_is_frozen():
    p = get_policy()
    with pytest.raises(ValidationError):
        p.meta.version = "9"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        p.return_7day.window_days = 99  # type: ignore[misc]


def test_policy_loaded_values():
    p = get_policy()
    assert p.meta.cutover_date == date(2026, 10, 1)
    assert p.return_7day.window_days == 7
    assert p.business_calendar.holidays[4].from_ == date(2026, 10, 1)
    assert p.payment_channels.mobile_pay_a == "悦付"


def test_env_override_for_mutation_tests(tmp_path, monkeypatch):
    raw = yaml.safe_load((ROOT / "policy" / "policy.yaml").read_text(encoding="utf-8"))
    raw["return_7day"]["window_days"] = 9
    f = tmp_path / "mutant.yaml"
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("XC_POLICY_PATH", str(f))
    assert get_policy().return_7day.window_days == 9
    monkeypatch.delenv("XC_POLICY_PATH")
    assert get_policy().return_7day.window_days == 7


def test_unknown_key_is_rejected(tmp_path):
    raw = yaml.safe_load((ROOT / "policy" / "policy.yaml").read_text(encoding="utf-8"))
    raw["meta"]["typo_key"] = 1
    f = tmp_path / "bad.yaml"
    f.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy(f)


# ------------------------------------------------------------ 时区
def test_to_bj_handles_naive_and_utc():
    assert to_bj(datetime(2026, 11, 1, 10, 0)) == bj(2026, 11, 1, 10)
    utc = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
    assert to_bj(utc) == bj(2026, 10, 1, 0, 0)
    assert to_bj(utc).utcoffset().total_seconds() == 8 * 3600


# ------------------------------------------------------------ 业务签收时间（C4）
def test_business_signed_at_uses_locker_or_station_in_time():
    delivered = bj(2026, 10, 26, 9, 0)
    in_time = bj(2026, 10, 23, 18, 0)
    assert business_signed_at(delivered, "station", in_time) == in_time
    assert business_signed_at(delivered, "locker", in_time) == in_time
    assert business_signed_at(delivered, "hand", in_time) == delivered
    assert business_signed_at(delivered, "proxy", in_time) == delivered
    assert business_signed_at(delivered, "locker", None) == delivered


# ------------------------------------------------------------ 第 k 天
def test_day_k_after_counts_from_next_day():
    signed = bj(2026, 10, 25, 14, 10)
    assert day_k_after(signed, bj(2026, 10, 25, 23, 59, 59)) == 0
    assert day_k_after(signed, bj(2026, 10, 26, 0, 0, 0)) == 1
    assert day_k_after(signed, bj(2026, 11, 1, 23, 59, 59)) == 7
    assert day_k_after(signed, bj(2026, 11, 2, 0, 0, 0)) == 8
    assert day_k_after(signed, bj(2026, 10, 20)) == 0  # 时钟早于签收不为负


def test_day_k_from_payment_starts_at_one():
    paid = bj(2026, 10, 26, 8, 0)
    assert day_k_from_payment(paid, bj(2026, 10, 26, 22, 0)) == 1
    assert day_k_from_payment(paid, bj(2026, 11, 1, 10)) == 7
    assert day_k_from_payment(paid, bj(2026, 11, 2, 0, 0)) == 8


def test_within_hours_is_inclusive_and_real_elapsed():
    start = bj(2026, 10, 30, 10, 0)
    assert within_hours(start, bj(2026, 11, 1, 10, 0), 48)
    assert not within_hours(start, bj(2026, 11, 1, 10, 0, 1), 48)
    assert not within_hours(start, bj(2026, 10, 30, 9, 59, 59), 48)  # 早于起点


# ------------------------------------------------------------ 工作日（2026-11-01 为周日）
@pytest.mark.parametrize(
    "d, expected",
    [
        (date(2026, 11, 1), False),  # 周日
        (date(2026, 10, 31), False),  # 周六
        (date(2026, 11, 2), True),  # 周一
        (date(2026, 10, 1), False),  # 国庆假期首日（周四）
        (date(2026, 10, 7), False),  # 假期末日
        (date(2026, 10, 8), True),  # 假期后第一天（周四）
        (date(2026, 9, 30), True),  # 假期前一天（周三）
        (date(2026, 2, 23), False),  # 春节假期末日恰为周一
        (date(2026, 2, 24), True),
    ],
)
def test_is_business_day(d, expected):
    assert is_business_day(d) is expected


def test_add_business_days_from_sunday_lands_on_monday():
    assert add_business_days(bj(2026, 11, 1, 10), 1) == bj(2026, 11, 2, 10)


def test_add_business_days_skips_weekend_and_holiday():
    assert add_business_days(bj(2026, 10, 30, 9, 30), 1) == bj(2026, 11, 2, 9, 30)  # 周五 -> 周一
    assert add_business_days(bj(2026, 9, 30, 12), 1) == bj(2026, 10, 8, 12)  # 国庆后第一个工作日
    assert add_business_days(bj(2026, 9, 30, 12), 2) == bj(2026, 10, 9, 12)
    assert add_business_days(bj(2026, 2, 13, 12), 1) == bj(2026, 2, 24, 12)  # 春节
    assert add_business_days(bj(2026, 11, 2, 10), -1) == bj(2026, 10, 30, 10)  # 往回减
    assert add_business_days(bj(2026, 11, 1, 10), 0) == bj(2026, 11, 1, 10)


def test_add_business_days_preserves_timezone():
    assert add_business_days(bj(2026, 11, 3, 8), 3).utcoffset().total_seconds() == 8 * 3600


# ------------------------------------------------------------ 月份运算
def test_add_months_clamps_to_month_end():
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 11, 2), 12) == date(2026, 11, 2)
    assert add_months(date(2026, 1, 1), -12) == date(2025, 1, 1)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


# ------------------------------------------------------------ 客服时段（HND-01）
@pytest.mark.parametrize(
    "h, m, expected",
    [(8, 59, False), (9, 0, True), (21, 59, True), (22, 0, False), (22, 30, False), (3, 0, False)],
)
def test_in_service_hours(h, m, expected):
    assert in_service_hours(bj(2026, 11, 1, h, m)) is expected
