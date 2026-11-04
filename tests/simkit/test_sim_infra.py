"""/_sim 路由、时间接口、事件上报与去重、服务间客户端、格式工具、时钟、数据库。"""
import calendar
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from _support import SERVICES, Harness, logical
from sqlalchemy import text

from xcsim.simkit import clock, fmt
from xcsim.simkit.client import DependencyError, ServiceClient, TokenAuth
from xcsim.simkit.clock import SimClock
from xcsim.simkit.config import get_settings, parse_anchor
from xcsim.simkit.db import create_sqlite_engine, restore_from_snapshot
from xcsim.simkit.events import EventInbox, EventReporter


def internal(h: Harness, method: str, path: str, payload=None):
    token = get_settings().tokens["internal"]
    header = {"Authorization": f"Bearer {token}"}
    if h.service == "logistics":
        header = {"X-Express-Token": token}
    if h.service == "control":
        header = {"X-Control-Token": token}
    return h.client().request(method, h.sim(path), json=payload, headers=header, auth=None)


@pytest.fixture()
def h(tmp_path):
    return Harness("order", tmp_path)


# ------------------------------------------------------------ /_sim
def test_sim_routes_require_internal_token(h):
    for path in ("/sync", "/tick", "/reset", "/events"):
        cs = h.client("cs-agent").post(h.sim(path), json={})
        assert cs.status_code == 403
        none = h.client().post(h.sim(path), json={}, auth=None)
        assert none.status_code == 401


def test_sim_routes_are_not_in_openapi(h):
    spec = h.client().get("/order/openapi.json").json()
    assert not [p for p in spec["paths"] if "_sim" in p]
    assert "/order/health" in spec["paths"] and "/order/v1/time" in spec["paths"]


def test_sync_sets_clock_and_x_sim_now(h):
    resp = internal(h, "POST", "/sync", {"clock": {"sim_time": "2026-10-10T09:30:00+08:00", "mode": "frozen", "rate": 1}})
    assert resp.status_code == 200 and resp.json()["data"]["sim"]["sim_time"].startswith("2026-10-10T09:30:00")
    got = h.client().get(h.p("/thing"))
    assert got.headers["X-Sim-Now"] == "2026-10-10T09:30:00+08:00"


def test_tick_invokes_registered_callback_and_default_is_noop(h, tmp_path):
    resp = internal(h, "POST", "/tick", {"now": "2026-11-02T10:00:00+08:00"})
    assert resp.json()["data"] == {"processed": 7}
    assert h.ticks == [datetime(2026, 11, 2, 10, 0, tzinfo=timezone(timedelta(hours=8)))]
    from xcsim.simkit.service import build_app

    bare = Harness("catalog", tmp_path)
    bare.app = build_app(service="catalog", title="t", routers=[])
    assert internal(bare, "POST", "/tick", {}).json() == {"processed": 0}


def test_events_are_deduplicated_by_seq_and_ordered(h):
    ev = lambda n: {"seq": n, "type": "parcel.delivered", "service": "logistics", "entity_id": f"P{n}"}
    r1 = internal(h, "POST", "/events", {"events": [ev(2), ev(1)]}).json()["data"]
    assert r1 == {"accepted": 2, "duplicates": 0, "last_seq": 2}
    r2 = internal(h, "POST", "/events", {"events": [ev(2), ev(3), ev(3)]}).json()["data"]
    assert r2 == {"accepted": 1, "duplicates": 2, "last_seq": 3}
    assert [e["seq"] for e in h.events] == [1, 2, 3]  # 按 seq 升序、各处理一次


def test_reset_clears_faults_and_inbox_and_calls_callback(h):
    internal(h, "POST", "/sync", {"faults": [{"id": "f", "kind": "http_503"}], "profile": "realistic"})
    internal(h, "POST", "/events", {"events": [{"seq": 1, "type": "x", "service": "s", "entity_id": "e"}]})
    assert internal(h, "POST", "/reset", {"seed": 99}).status_code == 200
    sim = h.app.state.sim
    assert sim.faults.rules() == [] and sim.faults.profile == "clean" and sim.faults.seed == 99
    assert sim.inbox.last_seq == 0 and h.resets == [{"seed": 99, "profile": None}]
    # 同一个 seq 在 reset 之后可以重新接收
    again = internal(h, "POST", "/events", {"events": [{"seq": 1, "type": "x", "service": "s", "entity_id": "e"}]})
    assert again.json()["data"]["accepted"] == 1


def test_sync_rejects_unknown_profile(h):
    assert internal(h, "POST", "/sync", {"profile": "weird"}).status_code == 422


def test_event_inbox_unit():
    inbox = EventInbox()
    seen = []
    inbox.handler = seen.append
    assert inbox.receive([{"seq": 5}, {"seq": 5}])["duplicates"] == 1
    assert [e["seq"] for e in seen] == [5]


# ------------------------------------------------------------ health / time（各服务格式不同）
EPOCH_MS = calendar.timegm((2026, 11, 1, 2, 0, 0)) * 1000  # 2026-11-01 10:00 +08:00 = 02:00 UTC


@pytest.mark.parametrize("service", SERVICES)
def test_health_and_time_per_service(service, tmp_path):
    hh = Harness(service, tmp_path)
    c = hh.client()
    assert c.get(f"/{service}/health", auth=None).json() == {"status": "ok", "service": service}  # health 不要鉴权
    body = c.get(f"/{service}/v1/time", auth=None).json()
    data = {"order": body.get("data"), "aftersale": body.get("data"), "logistics": body.get("data"), "member": body.get("result")}.get(service, body)
    if service in ("catalog", "order", "aftersale", "ticket", "control"):
        assert data["now"] == "2026-11-01T10:00:00+08:00"
    elif service == "logistics":
        assert data["serverTime"] == "2026-11-01 10:00:00"
    elif service == "payment":
        assert data["now_ms"] == EPOCH_MS
    else:
        assert data["now"] == "2026-11-01T02:00:00Z"
    assert data["mode"] == "frozen"


def test_time_follows_clock_changes(h):
    clock.clock.set(datetime(2026, 12, 12, 8, 0, tzinfo=timezone.utc))
    assert h.client().get("/order/v1/time", auth=None).json()["data"]["now"] == "2026-12-12T16:00:00+08:00"


def test_every_response_has_request_id_and_sim_now_even_errors(h):
    for resp in (h.client().get(h.p("/nope")), h.client().get(h.p("/thing"), auth=None), h.client().get("/order/health")):
        assert resp.headers["X-Request-Id"] and resp.headers["X-Sim-Now"]
    ids = {h.client().get(h.p("/thing")).headers["X-Request-Id"] for _ in range(3)}
    assert len(ids) == 3


# ------------------------------------------------------------ 事件上报 / 服务间客户端
def test_event_reporter_posts_to_control_with_internal_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("X-Control-Token")
        seen["body"] = request.read()
        return httpx.Response(200, json={"seq": 5})

    rep = EventReporter("payment", ServiceClient("control", transport=httpx.MockTransport(handler), base_url="http://control.test"))
    out = rep.report("refund.succeeded", "refund", "R1", {"amount_fen": 100})
    assert out == {"seq": 5}
    assert seen["url"] == "http://control.test/control/_sim/report"
    assert seen["auth"] == get_settings().tokens["internal"]
    import json

    body = json.loads(seen["body"])
    assert body["type"] == "refund.succeeded" and body["service"] == "payment"
    assert body["sim_time"] == "2026-11-01T10:00:00+08:00" and "seq" not in body


def test_event_reporter_swallows_failures():
    def boom(request):
        raise httpx.ConnectError("down")

    rep = EventReporter("payment", ServiceClient("control", transport=httpx.MockTransport(boom), base_url="http://x.test"))
    assert rep.report("t", "e", "1") is None


def test_service_client_picks_header_by_target_service():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    token = get_settings().tokens["internal"]
    for svc, header, value in [
        ("order", "authorization", f"Bearer {token}"),
        ("logistics", "x-express-token", token),
        ("control", "x-control-token", token),
    ]:
        seen.clear()
        ServiceClient(svc, transport=httpx.MockTransport(handler), base_url="http://h.test").get(f"/{svc}/v1/x")
        assert seen[header] == value


def test_service_client_wraps_network_errors_and_sends_idempotency_key():
    def boom(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(DependencyError) as ei:
        ServiceClient("logistics", transport=httpx.MockTransport(boom), base_url="http://x.test").get("/logistics/v1/x")
    assert ei.value.service == "logistics"
    keys = []
    ServiceClient("payment", transport=httpx.MockTransport(lambda r: (keys.append(r.headers["idempotency-key"]), httpx.Response(200))[1]), base_url="http://x.test").post("/payment/v1/refunds", idempotency_key="abc", json={})
    assert keys == ["abc"]


def test_service_base_url_comes_from_config(monkeypatch):
    from xcsim.simkit.config import service_base_url

    monkeypatch.setenv("XC_BASE_URL", "http://127.0.0.1:9000/")
    assert service_base_url("order") == "http://127.0.0.1:9000"
    monkeypatch.setenv("XC_ORDER_URL", "http://order:8101")
    assert service_base_url("order") == "http://order:8101"
    assert service_base_url("payment") == "http://127.0.0.1:9000"


def test_token_auth_uses_path_segment():
    req = httpx.Request("GET", "http://h/logistics/v1/trace")
    next(TokenAuth("t").auth_flow(req))
    assert req.headers["x-express-token"] == "t" and "authorization" not in req.headers


# ------------------------------------------------------------ 格式工具
DT = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def test_time_formats():
    assert fmt.iso_bj(DT) == "2026-11-01T10:00:00+08:00"
    assert fmt.iso_bj(DT.astimezone(timezone.utc)) == "2026-11-01T10:00:00+08:00"
    assert fmt.utc_z(DT) == "2026-11-01T02:00:00Z"
    assert fmt.epoch_ms(DT) == EPOCH_MS
    assert fmt.epoch_ms(DT.replace(microsecond=123456)) == EPOCH_MS + 123
    assert fmt.bj_str(DT) == "2026-11-01 10:00:00"
    assert fmt.bj_str(datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)) == "2026-11-01 10:00:00"
    assert fmt.iso_bj(datetime(2026, 11, 1, 10, 0)) == "2026-11-01T10:00:00+08:00"  # naive 视为北京时间


def test_time_parsing_roundtrip():
    assert fmt.parse_dt("2026-11-01T02:00:00Z") == DT
    assert fmt.parse_dt("2026-11-01 10:00:00") == DT
    assert fmt.parse_dt("2026-11-01T10:00:00+08:00") == DT
    assert fmt.from_epoch_ms(EPOCH_MS + 45) == DT.replace(microsecond=45000)


@pytest.mark.parametrize(
    "fen, text, number",
    [(9900, "99.00", 99.0), (5, "0.05", 0.05), (0, "0.00", 0.0), (-150, "-1.50", -1.5), (12345, "123.45", 123.45), (100, "1.00", 1.0)],
)
def test_money_formats(fen, text, number):
    assert fmt.yuan_str(fen) == text
    assert fmt.yuan_float(fen) == number
    assert fmt.fen_int(fen) == fen
    assert fmt.parse_yuan(text) == fen


def test_parse_yuan_rejects_sub_fen_precision():
    assert fmt.parse_yuan(99) == 9900 and fmt.parse_yuan("0.1") == 10 and fmt.parse_yuan(1.5) == 150
    with pytest.raises(ValueError):
        fmt.parse_yuan("0.001")


# ------------------------------------------------------------ 时钟
def test_clock_frozen_by_default_and_advance_set():
    t = [100.0]
    c = SimClock(DT, wall=lambda: t[0])
    t[0] += 3600  # 墙钟走了，仿真时间不动
    assert c.now() == DT and c.mode == "frozen"
    assert c.advance(90) == DT + timedelta(seconds=90)
    c.set(DT - timedelta(days=30))
    assert c.now() == DT - timedelta(days=30)


def test_clock_running_uses_rate():
    t = [0.0]
    c = SimClock(DT, wall=lambda: t[0])
    c.set(DT, mode="running", rate=60)
    t[0] += 10
    assert c.now() == DT + timedelta(minutes=10)
    c.set(c.now(), mode="frozen")
    t[0] += 1000
    assert c.now() == DT + timedelta(minutes=10)


def test_clock_apply_snapshot_and_validation():
    c = SimClock(DT)
    c.apply({"sim_time": "2026-10-10T09:00:00+08:00", "mode": "running", "rate": 2})
    assert c.mode == "running" and c.rate == 2 and c.snapshot()["sim_time"].startswith("2026-10-10T09:00")
    with pytest.raises(ValueError):
        c.set(DT, mode="paused")
    with pytest.raises(ValueError):
        c.set(DT, rate=0)


def test_clock_reset_returns_to_anchor_from_env(monkeypatch):
    monkeypatch.setenv("XC_ANCHOR", "2026-12-12T08:00:00+08:00")
    clock.clock.set(DT, mode="running", rate=5)
    clock.clock.reset()
    assert clock.now() == parse_anchor("2026-12-12T08:00:00+08:00") and clock.clock.mode == "frozen"
    assert parse_anchor("2026-11-01T10:00:00").utcoffset() == timedelta(hours=8)


# ------------------------------------------------------------ 数据库
def test_engine_uses_wal_and_foreign_keys(tmp_path):
    e = create_sqlite_engine(tmp_path / "a.db")
    with e.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    e.dispose()


def test_restore_from_snapshot_while_other_handles_are_open(tmp_path):
    snap = tmp_path / "snap.db"
    s = sqlite3.connect(snap)
    s.execute("create table t(id integer primary key, v text)")
    s.executemany("insert into t values (?, ?)", [(1, "原始"), (2, "原始")])
    s.commit()
    s.close()

    engine = create_sqlite_engine(tmp_path / "live.db")
    with engine.begin() as c:
        c.execute(text("create table t(id integer primary key, v text)"))
        c.execute(text("insert into t values (1, '被改过'), (9, '多余')"))
    held = sqlite3.connect(tmp_path / "live.db")  # 模拟别处仍持有句柄（Windows 下不能覆盖文件）
    held.execute("select * from t").fetchall()

    restore_from_snapshot(engine, snap)

    with engine.connect() as c:
        rows = c.execute(text("select id, v from t order by id")).all()
    assert rows == [(1, "原始"), (2, "原始")]
    assert held.execute("select count(*) from t").fetchone() == (2,)  # 旁路连接也看到恢复后的数据
    held.close()
    # 恢复后引擎仍可写
    with engine.begin() as c:
        c.execute(text("insert into t values (3, '新')"))
    engine.dispose()


def test_restore_missing_snapshot_raises(tmp_path):
    e = create_sqlite_engine(tmp_path / "x.db")
    with pytest.raises(FileNotFoundError):
        restore_from_snapshot(e, tmp_path / "none.db")


# ------------------------------------------------------------ UTF-8（F7）
def test_entrypoint_forces_utf8_stdout_even_when_locale_is_not():
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONUTF8"] = "0"
    out = subprocess.run(
        [sys.executable, "-c", "import xcsim; print('星川商城')"], capture_output=True, env=env, check=True
    ).stdout
    assert out.decode("utf-8").strip() == "星川商城"
