"""故障注入：每种 kind x 每种错误风格；timeout_after_commit 确实先提交；概率可复现；realistic 配置档。"""
import json
import time

import pytest
from _support import DATA_PREFIX, SERVICES, Harness, expected, logical
from pydantic import ValidationError

from xcsim.simkit.faults import FaultRegistry, FaultRule, TokenBucket, corrupt_body
from xcsim.simkit.config import get_settings


def internal_post(h: Harness, path: str, payload: dict):
    token = get_settings().tokens["internal"]
    header = {"X-Express-Token": token} if h.service == "logistics" else (
        {"X-Control-Token": token} if h.service == "control" else {"Authorization": f"Bearer {token}"}
    )
    return h.client().post(h.sim(path), json=payload, headers=header, auth=None)


def set_faults(h: Harness, *rules: dict, profile: str | None = None, seed: int | None = None):
    payload: dict = {"faults": list(rules)}
    if profile:
        payload["profile"] = profile
    if seed is not None:
        payload["seed"] = seed
    resp = internal_post(h, "/sync", payload)
    assert resp.status_code == 200, resp.text
    return resp


def rule(h: Harness, rid="f1", **kw) -> dict:
    base = {"id": rid, "service": h.service, "method": "GET", "path": h.p("/thing"), "kind": "http_503"}
    base.update(kw)
    return base


def dig(doc, dotted: str):
    for part in dotted.split("."):
        if part:
            doc = doc[part]
    return doc


@pytest.fixture()
def harness(tmp_path):
    def get(service):
        d = tmp_path / service
        d.mkdir(exist_ok=True)
        return Harness(service, d)

    return get


# ------------------------------------------------------------ 每种 kind x 每种风格
@pytest.mark.parametrize("service", SERVICES)
def test_http_503_in_service_style(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="http_503"))
    resp = h.client().get(h.p("/thing"))
    assert logical(service, resp) == expected(service, 503)
    assert h.hits == 0  # 没执行业务


@pytest.mark.parametrize("service", SERVICES)
def test_http_429_with_retry_after_in_service_style(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="http_429", retry_after_s=7))
    resp = h.client().get(h.p("/thing"))
    assert logical(service, resp) == expected(service, 429)
    assert resp.headers["Retry-After"] == "7"
    assert h.hits == 0


@pytest.mark.parametrize("service", SERVICES)
def test_timeout_hangs_then_504_without_running_handler(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="timeout", hold_ms=150))
    t0 = time.monotonic()
    resp = h.client().get(h.p("/thing"))
    assert time.monotonic() - t0 >= 0.14
    assert logical(service, resp) == expected(service, 504)
    assert h.hits == 0


@pytest.mark.parametrize("service", SERVICES)
def test_timeout_after_commit_runs_handler_first(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="timeout_after_commit", hold_ms=100, method="POST", path=h.p("/notes")))
    t0 = time.monotonic()
    resp = h.client().post(h.p("/notes"), json={"text": "x"}, headers={"Idempotency-Key": "k-commit"})
    assert time.monotonic() - t0 >= 0.09
    assert logical(service, resp) == expected(service, 504)
    assert h.note_count() == 1  # 响应是 504，但数据已经提交


@pytest.mark.parametrize("service", SERVICES)
def test_latency_delays_but_succeeds(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="latency", latency_ms=200))
    t0 = time.monotonic()
    resp = h.client().get(h.p("/thing"))
    assert time.monotonic() - t0 >= 0.19
    assert resp.status_code == 200 and h.hits == 1


def test_latency_jitter_stays_within_bounds():
    reg = FaultRegistry("catalog", seed=1)
    reg.replace([FaultRule(id="l", kind="latency", latency_ms=100, jitter_ms=20)])
    delays = [reg.evaluate("GET", "/x", None, None).delay_s for _ in range(200)]
    assert all(0.08 - 1e-9 <= d <= 0.12 + 1e-9 for d in delays)
    assert len(set(delays)) > 50  # 确实有抖动


def test_timeout_after_commit_then_retry_with_same_key_returns_one_row(harness):
    h = harness("payment")
    set_faults(h, rule(h, kind="timeout_after_commit", hold_ms=10, method="POST", path=h.p("/notes"), times=1))
    c = h.client()
    first = c.post(h.p("/notes"), json={"text": "refund"}, headers={"Idempotency-Key": "k1"})
    assert first.status_code == 504
    again = c.post(h.p("/notes"), json={"text": "refund"}, headers={"Idempotency-Key": "k1"})
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true"
    assert h.note_count() == 1
    # 换新键重试就会重复写入：这正是练习要让学习者踩到的坑
    c.post(h.p("/notes"), json={"text": "refund"}, headers={"Idempotency-Key": "k2"})
    assert h.note_count() == 2


# ------------------------------------------------------------ corrupt 的全部 mode x 每种风格
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("mode", ["drop_field", "null_field", "wrong_type", "stale_status", "duplicate_item"])
def test_corrupt_field_modes(service, mode, harness):
    h = harness(service)
    prefix = DATA_PREFIX[service]
    field = f"{prefix}items" if mode == "duplicate_item" else f"{prefix}status"
    set_faults(h, rule(h, kind="corrupt", mode=mode, field=field))
    resp = h.client().get(h.p("/thing"))
    assert resp.status_code == 200
    doc = resp.json()
    parent = dig(doc, prefix.rstrip(".")) if prefix else doc
    if mode == "drop_field":
        assert "status" not in parent and "name" in parent
    elif mode == "null_field":
        assert parent["status"] is None
    elif mode == "wrong_type":
        assert parent["status"] == 0 and isinstance(parent["status"], int)
    elif mode == "stale_status":
        assert parent["status"] == "shipped"  # delivered 的上一个状态
    else:
        assert parent["items"] == [{"n": 1}, {"n": 1}, {"n": 2}]


@pytest.mark.parametrize("service", SERVICES)
def test_corrupt_truncated_json(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="corrupt", mode="truncated_json"))
    resp = h.client().get(h.p("/thing"))
    assert resp.status_code == 200
    with pytest.raises(ValueError):
        resp.json()
    assert h.hits == 1  # 业务执行了，只是响应被截断


@pytest.mark.parametrize("service", SERVICES)
def test_corrupt_garbled_encoding(service, harness):
    h = harness(service)
    set_faults(h, rule(h, kind="corrupt", mode="garbled_encoding"))
    resp = h.client().get(h.p("/thing"))
    assert resp.status_code == 200
    assert "星川" not in resp.text
    assert "æ" in resp.text  # UTF-8 被当 latin-1 读出来的典型乱码
    assert dig(resp.json(), DATA_PREFIX[service].rstrip("."))["count"] == 3  # 数字不受影响


def test_corrupt_applies_to_every_list_element():
    raw = json.dumps({"data": {"traces": [{"t": 1, "k": "a"}, {"t": 2, "k": "b"}]}}).encode()
    out = json.loads(corrupt_body(raw, "drop_field", "data.traces.t"))
    assert out["data"]["traces"] == [{"k": "a"}, {"k": "b"}]
    out = json.loads(corrupt_body(raw, "null_field", "data.traces.0.k"))
    assert out["data"]["traces"] == [{"t": 1, "k": None}, {"t": 2, "k": "b"}]


def test_corrupt_missing_field_is_noop():
    raw = json.dumps({"a": 1}).encode()
    assert json.loads(corrupt_body(raw, "drop_field", "zzz")) == {"a": 1}


def test_corrupt_does_not_touch_error_responses(harness):
    h = harness("catalog")
    set_faults(h, rule(h, kind="corrupt", mode="garbled_encoding"))
    resp = h.client().get(h.p("/thing"), headers={"Authorization": "Bearer bad"}, auth=None)
    assert resp.status_code == 401 and resp.json()["errorCode"] == "UNAUTHORIZED"


# ------------------------------------------------------------ 规则匹配
def test_times_limits_number_of_firings(harness):
    h = harness("catalog")
    set_faults(h, rule(h, times=2))
    codes = [h.client().get(h.p("/thing")).status_code for _ in range(4)]
    assert codes == [503, 503, 200, 200]


def test_repeated_identical_sync_does_not_reset_times(harness):
    h = harness("catalog")
    r = rule(h, times=1)
    set_faults(h, r)
    assert h.client().get(h.p("/thing")).status_code == 503
    set_faults(h, r)  # control 重复推送同一份规则
    assert h.client().get(h.p("/thing")).status_code == 200


def test_changed_rule_is_rearmed(harness):
    h = harness("catalog")
    set_faults(h, rule(h, times=1))
    assert h.client().get(h.p("/thing")).status_code == 503
    set_faults(h, rule(h, times=1, retry_after_s=9))
    assert h.client().get(h.p("/thing")).status_code == 503


def test_match_body_and_caller_and_method_filters(harness):
    h = harness("order")
    set_faults(h, rule(h, method="POST", path=h.p("/notes"), match_body={"text": "target"}, caller="cs-agent", kind="http_503"))
    c = h.client()
    ok = c.post(h.p("/notes"), json={"text": "other"}, headers={"Idempotency-Key": "a"})
    hit = c.post(h.p("/notes"), json={"text": "target"}, headers={"Idempotency-Key": "b"})
    internal = h.client("internal").post(h.p("/notes"), json={"text": "target"}, headers={"Idempotency-Key": "c"})
    get = c.get(h.p("/thing"))
    assert (ok.status_code, hit.status_code, internal.status_code, get.status_code) == (201, 503, 201, 200)


def test_path_template_and_other_service_rules_are_ignored(harness):
    h = harness("catalog")
    set_faults(
        h,
        rule(h, rid="a", path=h.p("/things/{id}")),
        rule(h, rid="b", service="payment", path=h.p("/thing")),
    )
    assert h.client().get(h.p("/things/42")).status_code == 503
    # a 的模板只匹配 /things/*；b 属于别的服务。两者都不影响 /thing
    assert h.client().get(h.p("/thing")).status_code == 200


def test_foreign_service_rule_does_not_apply(harness):
    h = harness("catalog")
    set_faults(h, rule(h, service="payment"))
    assert h.client().get(h.p("/thing")).status_code == 200


def test_faults_do_not_hit_health_time_docs_and_sim(harness):
    h = harness("catalog")
    set_faults(h, {"id": "all", "service": "*", "kind": "http_503"})
    c = h.client()
    assert c.get(f"/catalog/health").status_code == 200
    assert c.get(f"/catalog/v1/time").status_code == 200
    assert c.get("/catalog/openapi.json").status_code == 200
    assert internal_post(h, "/sync", {"faults": []}).status_code == 200  # 还能把故障清掉
    assert c.get(h.p("/thing")).status_code == 200


# ------------------------------------------------------------ 可复现的概率
def _pattern(seed: int, n=60, probability=0.5):
    reg = FaultRegistry("payment", seed)
    reg.replace([FaultRule(id="p", kind="http_503", probability=probability)])
    return [reg.evaluate("GET", "/x", None, None).kind == "http_503" for _ in range(n)]


def test_probability_is_reproducible_per_seed():
    assert _pattern(7) == _pattern(7)
    assert _pattern(7) != _pattern(8)


def test_probability_roughly_matches():
    hits = sum(_pattern(3, n=2000, probability=0.3))
    assert 480 <= hits <= 720  # 2000 * 0.3 = 600，宽容差


def test_probability_extremes():
    assert all(_pattern(1, probability=1.0))
    assert not any(_pattern(1, probability=0.0))


def test_each_rule_has_its_own_stream():
    reg = FaultRegistry("payment", 42)
    reg.replace([FaultRule(id="a", kind="http_503", probability=0.5, path="/a"), FaultRule(id="b", kind="http_503", probability=0.5, path="/b")])
    only_a = [reg.evaluate("GET", "/a", None, None).kind for _ in range(30)]
    reg2 = FaultRegistry("payment", 42)
    reg2.replace([FaultRule(id="a", kind="http_503", probability=0.5, path="/a")])
    assert only_a == [reg2.evaluate("GET", "/a", None, None).kind for _ in range(30)]  # b 的存在不影响 a 的序列


# ------------------------------------------------------------ 规则校验
def test_fault_rule_validation():
    with pytest.raises(ValidationError):
        FaultRule(id="x", kind="corrupt")  # 缺 mode
    with pytest.raises(ValidationError):
        FaultRule(id="x", kind="corrupt", mode="drop_field")  # 缺 field
    with pytest.raises(ValidationError):
        FaultRule(id="x", kind="http_503", probability=1.5)
    with pytest.raises(ValidationError):
        FaultRule(id="x", kind="explode")
    FaultRule(id="x", kind="corrupt", mode="truncated_json")  # 这两种 mode 不需要 field
    FaultRule(id="x", kind="corrupt", mode="garbled_encoding")


# ------------------------------------------------------------ realistic 配置档
def test_clean_profile_is_default_and_noise_free(harness):
    h = harness("logistics")
    assert h.app.state.sim.faults.profile == "clean"
    t0 = time.monotonic()
    assert all(h.client().get(h.p("/thing")).status_code == 200 for _ in range(10))
    assert time.monotonic() - t0 < 2


def test_realistic_latency_percentiles():
    reg = FaultRegistry("order", 5)
    samples = sorted(reg._latency_ms() for _ in range(6000))
    p50, p95 = samples[len(samples) // 2], samples[int(len(samples) * 0.95)]
    assert 70 <= p50 <= 92
    assert 520 <= p95 <= 700


def test_realistic_logistics_is_about_three_times_slower():
    a, b = FaultRegistry("order", 5), FaultRegistry("logistics", 5)
    sa = sorted(a._latency_ms() for _ in range(4000))[2000]
    sb = sorted(b._latency_ms() for _ in range(4000))[2000]
    assert 2.5 <= sb / sa <= 3.5


def test_realistic_adds_503_at_configured_rate():
    reg = FaultRegistry("order", 9)
    reg.set_profile("realistic")
    reg.realistic.median_ms = 0
    reg.realistic.bucket_capacity = 10**9
    reg._bucket = TokenBucket(10**9, 10**9)
    hits = sum(reg.evaluate("GET", "/x", "internal", None).kind == "http_503" for _ in range(20000))
    assert 60 <= hits <= 140  # 0.5% * 20000 = 100


def test_realistic_profile_over_http_sets_delay_and_503(harness):
    h = harness("catalog")
    set_faults(h, profile="realistic")
    reg = h.app.state.sim.faults
    assert reg.profile == "realistic"
    reg.realistic.median_ms = 0
    reg.realistic.error_503_rate = 1.0
    assert logical("catalog", h.client().get(h.p("/thing"))) == expected("catalog", 503)
    set_faults(h, profile="clean")
    assert h.client().get(h.p("/thing")).status_code == 200


def test_realistic_rate_limit_is_per_token_and_returns_429(harness):
    h = harness("order")
    set_faults(h, profile="realistic")
    reg = h.app.state.sim.faults
    reg.realistic.median_ms = 0
    reg.realistic.error_503_rate = 0.0
    reg._bucket = TokenBucket(2, 0.01)
    c = h.client()
    codes = [c.get(h.p("/thing")).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]
    limited = c.get(h.p("/thing"))
    assert int(limited.headers["Retry-After"]) >= 1
    assert logical("order", limited) == expected("order", 429)
    # internal token 不限流（服务间调用）
    assert h.client("internal").get(h.p("/thing")).status_code == 200


def test_token_bucket_refills_over_time():
    t = [0.0]
    b = TokenBucket(2, 1.0, now=lambda: t[0])
    assert b.take("k") == 0 and b.take("k") == 0
    wait = b.take("k")
    assert wait == pytest.approx(1.0)
    t[0] += 1.0
    assert b.take("k") == 0
    assert b.take("other") == 0  # 不同 token 各自的桶
    t[0] += 100
    assert b.take("k") == 0 and b.take("k") == 0 and b.take("k") > 0  # 最多回满到容量


def test_reset_state_clears_rules_and_profile():
    reg = FaultRegistry("order", 1)
    reg.replace([FaultRule(id="a", kind="http_503")])
    reg.set_profile("realistic")
    reg.reset_state()
    assert reg.rules() == [] and reg.profile == "clean"
    with pytest.raises(ValueError):
        reg.set_profile("weird")
