"""幂等（§3.1）：缺失 400、同键同参回放、同键异参 422、并发 409、24 仿真小时过期。"""
import threading
from datetime import timedelta

import pytest
from _support import Harness, logical

from xcsim.simkit import clock


@pytest.fixture()
def h(tmp_path):
    return Harness("payment", tmp_path)


def post(h, key="k1", body=None, path="/notes", **kw):
    headers = {} if key is None else {"Idempotency-Key": key}
    return h.client().post(h.p(path), json=body if body is not None else {"text": "a"}, headers=headers, **kw)


@pytest.mark.parametrize("service, status", [("payment", 400), ("order", 400), ("logistics", 200), ("member", 400), ("catalog", 400)])
def test_missing_key_is_400_in_service_style(service, status, tmp_path):
    hh = Harness(service, tmp_path)
    resp = post(hh, key=None)
    assert resp.status_code == status
    expect_code = {"payment": "IDEMPOTENCY_KEY_REQUIRED", "order": 40002, "logistics": "B0007",
                   "member": "IDEMPOTENCY_KEY_REQUIRED", "catalog": "IDEMPOTENCY_KEY_REQUIRED"}[service]
    assert logical(service, resp)[1] == expect_code
    assert hh.note_count() == 0


def test_blank_key_is_treated_as_missing(h):
    assert post(h, key="   ").status_code == 400


def test_same_key_same_params_replays_original_result(h):
    first = post(h)
    again = post(h)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json()
    assert "Idempotent-Replayed" not in first.headers
    assert again.headers["Idempotent-Replayed"] == "true"
    assert h.note_count() == 1


def test_replay_is_independent_of_json_key_order(h):
    a = post(h, body={"text": "a", "n": 1})
    b = post(h, body={"n": 1, "text": "a"})
    assert b.headers["Idempotent-Replayed"] == "true" and b.json() == a.json()


def test_same_key_different_params_is_422(h):
    post(h, body={"text": "a"})
    resp = post(h, body={"text": "b"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert h.note_count() == 1


def test_same_key_different_path_is_422(h):
    post(h, path="/notes")
    other = post(h, path="/slow-notes")
    assert other.status_code == 422


def test_different_keys_are_independent(h):
    post(h, key="a")
    post(h, key="b")
    assert h.note_count() == 2


def test_replay_keeps_original_status_and_envelope(tmp_path):
    hh = Harness("order", tmp_path)
    first = post(hh)
    again = post(hh)
    assert again.status_code == 201
    assert again.json()["code"] == 0 and again.json()["data"] == first.json()["data"]
    assert again.json()["request_id"] == first.json()["request_id"]


def test_concurrent_same_key_gets_409_while_first_is_running(h):
    results = {}

    def first():
        results["first"] = post(h, key="slow", path="/slow-notes")

    t = threading.Thread(target=first)
    t.start()
    assert h.slow_started.wait(5)
    second = post(h, key="slow", path="/slow-notes")
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
    h.slow_release.set()
    t.join(10)
    assert results["first"].status_code == 201
    # 第一次完成后，同键同参回放
    third = post(h, key="slow", path="/slow-notes")
    assert third.status_code == 201 and third.headers["Idempotent-Replayed"] == "true"
    assert h.note_count() == 1


def test_expires_after_24_simulated_hours(h):
    post(h)
    clock.clock.advance(24 * 3600 - 1)
    assert post(h).headers.get("Idempotent-Replayed") == "true"
    clock.clock.advance(1)  # 恰好 24 仿真小时
    fresh = post(h)
    assert fresh.status_code == 201 and "Idempotent-Replayed" not in fresh.headers
    assert h.note_count() == 2


def test_expired_key_may_be_reused_with_different_params(h):
    post(h, body={"text": "a"})
    clock.clock.set(clock.now() + timedelta(days=2))
    assert post(h, body={"text": "b"}).status_code == 201


def test_failed_first_attempt_releases_the_key(h):
    h.fail_next = True
    bad = post(h)
    assert bad.status_code == 422
    assert h.note_count() == 0
    ok = post(h)  # 同键同参重试：可以正常执行
    assert ok.status_code == 201 and "Idempotent-Replayed" not in ok.headers
    assert h.note_count() == 1


def test_business_write_and_key_commit_together(h):
    post(h)
    from sqlalchemy import select

    from xcsim.simkit.idempotency import IdempotencyRecord

    with h.factory() as s:
        rec = s.scalar(select(IdempotencyRecord))
        assert rec.state == "done" and rec.response_status == 201
        assert rec.expires_at_ms - rec.created_at_ms == 24 * 3600 * 1000
