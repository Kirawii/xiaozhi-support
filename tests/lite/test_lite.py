"""简化版仿真环境的端到端测试：每个命名场景都通过 API 验证，外加幂等、时间旅行、异步退款、鉴权。"""
from __future__ import annotations

import os

os.environ["XC_AUTO_SETTLE_SECONDS"] = "0"  # 测试中退款只随仿真时钟推进

import pytest
from fastapi.testclient import TestClient

from xcsim.lite import app as A
from xcsim.lite.world import World

AG = {"Authorization": "Bearer agent-dev-token"}
CT = {"X-Control-Token": "control-dev-token"}
SCENARIOS = list(World.build(background=False).scenarios)


@pytest.fixture
def client():
    with TestClient(A.app) as c:
        c.post("/control/reset", headers=CT, json={})
        yield c


def _dig(obj, path):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


@pytest.mark.parametrize("sid", SCENARIOS)
def test_scenario_check(client, sid, monkeypatch):
    s = client.post(f"/control/scenarios/{sid}/prepare", headers=CT).json()["data"]
    for f in A.STATE["faults"]:
        f["hold_ms"] = min(f.get("hold_ms", 0), 50)  # 测试里不真等
    chk = s["check"]
    r = client.request(chk["method"], chk["path"], json=chk.get("body"), headers=AG | chk.get("headers", {}))
    assert r.status_code == chk["expect_status"], r.text
    if chk.get("expect"):
        body = r.json() if "success" not in r.text[:20] else r.json()
        for path, want in chk["expect"].items():
            assert _dig(body, path) == want, (path, r.text)


def test_scenario_users_have_no_future_records(client):
    w = World.build()
    for s in w.scenarios.values():
        for o in w.orders.values():
            if o["user_id"] == s["user_id"]:
                assert o["created_at"] <= s["clock"], s["id"]


def test_deterministic_world():
    assert World.build(seed=42).fingerprint() == World.build(seed=42).fingerprint()
    assert World.build(seed=42).fingerprint() != World.build(seed=43).fingerprint()


def test_auth(client):
    assert client.get("/api/v1/time").status_code == 401
    assert client.get("/api/v1/time", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert client.post("/control/reset", headers=AG).status_code == 403


def test_time_endpoint_uses_sim_clock(client):
    assert client.get("/api/v1/time", headers=AG).json()["data"]["now"] == "2026-11-01T10:00:00+08:00"
    client.post("/control/clock", headers=CT, json={"set": "2026-11-03T08:00:00+08:00"})
    assert client.get("/api/v1/time", headers=AG).json()["data"]["now"] == "2026-11-03T08:00:00+08:00"


def test_timeout_after_commit_then_idempotent_retry(client):
    s = client.post("/control/scenarios/RFD-08/prepare", headers=CT).json()["data"]
    A.STATE["faults"][0]["hold_ms"] = 10
    chk = s["check"]
    h = AG | chk["headers"]
    assert client.post(chk["path"], json=chk["body"], headers=h).status_code == 504
    listed = client.get(f"/api/v1/refunds?order_id={chk['body']['order_id']}", headers=AG).json()["data"]["items"]
    assert len(listed) == 1  # 超时但实际已创建
    r2 = client.post(chk["path"], json=chk["body"], headers=h)
    assert r2.status_code == 202 and r2.headers.get("Idempotent-Replayed") == "true"
    assert r2.json()["data"]["refund_id"] == listed[0]["refund_id"]
    other = client.post(chk["path"], json=chk["body"] | {"amount": "1.00"}, headers=h)
    assert other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    new_key = client.post(chk["path"], json=chk["body"], headers=AG | {"Idempotency-Key": "another"})
    assert new_key.status_code == 409


def test_refund_requires_key(client):
    s = client.post("/control/scenarios/RFD-01/prepare", headers=CT).json()["data"]
    r = client.post("/api/v1/refunds", json=s["check"]["body"], headers=AG)
    assert r.status_code == 400 and r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_refund_async_settles_with_time_travel(client):
    s = client.post("/control/scenarios/RFD-02/prepare", headers=CT).json()["data"]
    chk = s["check"]
    rid = client.post(chk["path"], json=chk["body"], headers=AG | chk["headers"]).json()["data"]["refund_id"]
    view = client.get(f"/api/v1/refunds/{rid}", headers=AG).json()["data"]
    assert view["status"] == "reviewing"
    assert view["manual_review_due_at"].startswith("2026-11-02T10:00")  # 周日 + 1 个工作日 = 周一
    client.post("/control/clock", headers=CT, json={"set": "2026-11-02T12:00:00+08:00"})
    view = client.get(f"/api/v1/refunds/{rid}", headers=AG).json()["data"]
    assert view["status"] == "succeeded" and view["arrival_eta"]


def test_control_settle_failure(client):
    s = client.post("/control/scenarios/RFD-01/prepare", headers=CT).json()["data"]
    chk = s["check"]
    rid = client.post(chk["path"], json=chk["body"], headers=AG | chk["headers"]).json()["data"]["refund_id"]
    client.post(f"/control/refunds/{rid}/settle", headers=CT, json={"result": "failed", "failure_code": "ACCOUNT_FROZEN"})
    v = client.get(f"/api/v1/refunds/{rid}", headers=AG).json()["data"]
    assert v["status"] == "failed" and v["failure_code"] == "ACCOUNT_FROZEN"


def test_time_travel_moves_logistics(client):
    s = client.post("/control/scenarios/ORD-02/prepare", headers=CT).json()["data"]
    uid, oid = s["user_id"], s["order_id"]
    assert client.get(f"/api/v1/orders/{oid}?user_id={uid}", headers=AG).json()["data"]["status"] == "shipped"
    client.post("/control/clock", headers=CT, json={"advance_minutes": 60 * 24 * 3})
    assert client.get(f"/api/v1/orders/{oid}?user_id={uid}", headers=AG).json()["data"]["status"] == "delivered"


def test_withdraw_counts_toward_limit(client):
    s = client.post("/control/scenarios/RET-10/prepare", headers=CT).json()["data"]
    body = s["check"]["body"]
    cid = client.post("/api/v1/aftersales", json=body, headers=AG).json()["data"]["case_id"]
    client.post(f"/api/v1/aftersales/{cid}/withdraw", json={"user_id": body["user_id"]}, headers=AG)
    cid2 = client.post("/api/v1/aftersales", json=body, headers=AG).json()["data"]["case_id"]
    client.post(f"/api/v1/aftersales/{cid2}/withdraw", json={"user_id": body["user_id"]}, headers=AG)
    assert client.post("/api/v1/aftersales", json=body, headers=AG).json()["code"] == "AS_APPLY_LIMIT"


def test_fiction_phone_and_brands():
    w = World.build()
    assert all(u["phone"].startswith("100") and len(u["phone"]) == 11 for u in w.users.values())
    blob = repr([w.spus, w.users, w.parcels])
    for bad in ("微信", "支付宝", "顺丰", "京东", "华为", "小米", "苹果"):
        assert bad not in blob
