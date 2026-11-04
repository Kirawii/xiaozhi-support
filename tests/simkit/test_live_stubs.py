"""活体测试：真实 uvicorn 单进程服务（8 个服务挂一起），逐个接口调用桩并校验契约。"""
import re
import uuid

import jsonschema
import pytest

from xcsim.simkit.config import SERVICES

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
BEIJING_ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+08:00$")


# ------------------------------------------------------------ 根据 OpenAPI 自动造请求
def deref(spec, schema):
    if "$ref" in schema:
        node = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return deref(spec, node)
    return schema


def sample(spec, schema, required_only=True):
    schema = deref(spec, schema)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if deref(spec, o).get("type") != "null"]
            return sample(spec, options[0], required_only)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        names = schema.get("required", []) if required_only else list(props)
        return {n: sample(spec, props[n], required_only) for n in names}
    if kind == "array":
        return []
    if kind == "integer":
        return max(schema.get("minimum", 1), 1)
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    return "x1"


def operations(spec):
    for path, item in spec["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                yield path, method, item[method]


def build_request(spec, path, method, op):
    params = {"header": {}, "query": {}, "path": {}}
    for p in op.get("parameters", []):
        if p["in"] == "path":
            params["path"][p["name"]] = "X1"
        elif p["in"] == "query" and p.get("required"):
            params["query"][p["name"]] = sample(spec, p["schema"])
    url = path.format(**params["path"])
    body = None
    if "requestBody" in op:
        body = sample(spec, op["requestBody"]["content"]["application/json"]["schema"])
    headers = {"Idempotency-Key": f"k-{uuid.uuid4().hex}"} if method in ("post", "put", "patch") else {}
    return url, params["query"], body, headers


def response_schema(spec, op):
    code = next(c for c in op["responses"] if c.startswith("2"))
    content = op["responses"][code].get("content", {}).get("application/json")
    return int(code), content["schema"] if content else None


def deref_wrapper(spec, schema):
    """把带 $ref 的 schema 包成可独立校验的文档（保留 components 供内部 $ref 解析）。"""
    return {**schema, "components": spec["components"]}


@pytest.fixture(scope="module")
def specs(anon_client):
    return {name: anon_client.get(f"/{name}/openapi.json").json() for name in SERVICES}


def all_ops(specs):
    """业务接口（不含 /health 和 /v1/time 这两个基础设施接口，它们不是桩）。"""
    for name, spec in specs.items():
        for path, method, op in operations(spec):
            if "infra" not in op.get("tags", []):
                yield name, spec, path, method, op


def test_every_service_exposes_docs_openapi_health_and_time(anon_client):
    for name in SERVICES:
        assert anon_client.get(f"/{name}/docs").status_code == 200
        spec = anon_client.get(f"/{name}/openapi.json").json()
        assert all(p.startswith(f"/{name}/") for p in spec["paths"]), name
        assert anon_client.get(f"/{name}/health").json() == {"status": "ok", "service": name}
        resp = anon_client.get(f"/{name}/v1/time")
        assert resp.status_code == 200 and "X-Sim-Now" in resp.headers and "X-Request-Id" in resp.headers
    assert set(anon_client.get("/").json()) == set(SERVICES)


def test_no_internal_sim_routes_in_any_openapi(specs):
    for name, spec in specs.items():
        assert not [p for p in spec["paths"] if "_sim" in p], name


def test_every_operation_returns_a_valid_stub(specs, client, control_client):
    seen = 0
    for name, spec, path, method, op in all_ops(specs):
        url, query, body, headers = build_request(spec, path, method, op)
        c = control_client if name == "control" else client
        resp = c.request(method.upper(), url, params=query, json=body, headers=headers)
        code, schema = response_schema(spec, op)
        assert resp.status_code == (200 if name == "logistics" else code), (name, method, url, resp.text)
        assert resp.headers["X-Stub"] == "true", (name, url)
        assert resp.headers["X-Request-Id"] and resp.headers["X-Sim-Now"]
        if schema is not None:
            jsonschema.validate(resp.json(), deref_wrapper(spec, schema))
        seen += 1
    assert seen >= 60  # 7 个服务 + control 共约 65 个操作


def test_operation_count_matches_design_tables(specs):
    expected = {"catalog": 5, "order": 7, "logistics": 6, "payment": 6, "member": 8, "aftersale": 9, "ticket": 7}
    for name, n in expected.items():
        business = [(p, m) for p, m, op in operations(specs[name]) if "infra" not in op.get("tags", [])]
        assert len(business) == n, (name, business)
    control = [p for p, m, op in operations(specs["control"]) if "infra" not in op.get("tags", [])]
    assert len(control) == 15  # §6.1：reset/clock x2/faults x3+1/profile/trigger/events/scenarios x2/prepare/verify/manifest


def test_write_operations_require_idempotency_key(specs, client, control_client):
    checked = 0
    for name, spec, path, method, op in all_ops(specs):
        if not any(p["name"] == "Idempotency-Key" for p in op.get("parameters", [])):
            continue
        url, query, body, _ = build_request(spec, path, method, op)
        resp = client.request(method.upper(), url, params=query, json=body)  # 不带键
        if name == "logistics":
            assert resp.json()["success"] == "false"
        else:
            assert resp.status_code == 400, (name, url, resp.text)
        checked += 1
    assert checked >= 15


# ------------------------------------------------------------ 鉴权（活体）
@pytest.mark.parametrize(
    "path, header",
    [
        ("/catalog/v1/spus", "Authorization"),
        ("/order/v1/orders?user_id=U1", "Authorization"),
        ("/logistics/v1/parcels?orderNo=X", "X-Express-Token"),
        ("/payment/v1/users/U1/balance", "Authorization"),
        ("/member/v1/users/U1", "Authorization"),
        ("/aftersale/v1/cases?user_id=U1", "Authorization"),
        ("/ticket/v1/queue", "Authorization"),
        ("/control/v1/clock", "X-Control-Token"),
    ],
)
def test_live_auth_401_and_403(path, header, anon_client, client, control_client):
    from xcsim.simkit.config import get_settings

    tokens = get_settings().tokens
    anon = anon_client.get(path)
    assert anon.status_code == (200 if path.startswith("/logistics") else 401)
    if path.startswith("/logistics"):
        assert anon.json()["success"] == "false" and anon.json()["errCode"] == "B0010"
    wrong = "cs-agent" if path.startswith("/control") else "control"
    value = tokens[wrong] if header != "Authorization" else f"Bearer {tokens[wrong]}"
    resp = anon_client.get(path, headers={header: value})
    if path.startswith("/logistics"):
        assert resp.json()["errCode"] == "B0011"
    elif path.startswith("/member"):
        assert resp.status_code == 403 and resp.json()["ok"] is False
    else:
        assert resp.status_code == 403
    ok_client = control_client if path.startswith("/control") else client
    assert ok_client.get(path).status_code == 200


def test_sim_routes_live_need_internal_token(client, internal_client, control_client):
    logistics = client.post("/logistics/_sim/tick", json={})  # 物流永远 200，但 body 里是 B0011
    assert logistics.json()["success"] == "false" and logistics.json()["errCode"] == "B0011"
    assert client.post("/order/_sim/tick", json={}).status_code == 403
    assert control_client.post("/order/_sim/tick", json={}).status_code == 403
    assert internal_client.post("/order/_sim/tick", json={}).json()["data"] == {"processed": 0}
    assert internal_client.post("/control/_sim/report", json={"type": "t", "service": "s", "entity_type": "e", "entity_id": "1"}).json() == {"seq": 1}
    assert client.post("/control/_sim/report", json={"type": "t", "service": "s", "entity_type": "e", "entity_id": "1"}).status_code == 403


# ------------------------------------------------------------ 各服务风格（活体抽查）
def test_catalog_style_camelcase_bare_money_string(client):
    body = client.get("/catalog/v1/skus/SKU-1").json()
    assert {"skuId", "spuId", "currentPrice", "priceType"} <= set(body)
    assert re.fullmatch(r"\d+\.\d\d", body["currentPrice"])
    hist = client.get("/catalog/v1/skus/SKU-1/price-history").json()["items"][0]
    assert BEIJING_ISO.match(hist["validFrom"])


def test_order_style_snake_envelope(client):
    body = client.get("/order/v1/orders/XC1", params={"user_id": "U1"}).json()
    assert body["code"] == 0 and body["request_id"].startswith("req_")
    d = body["data"]
    assert re.fullmatch(r"\d+\.\d\d", d["fees"]["total_payable"]) and BEIJING_ISO.match(d["created_at"])
    assert "parcels_summary" in d and "synced_at" in d


def test_logistics_style_always_200_numeric_codes_and_bj_time(client):
    body = client.get("/logistics/v1/trace", params={"mailNo": "QL1", "carrierCode": "QLSY"}).json()
    assert body["success"] == "true" and body["errCode"]
    t = body["data"]["traces"][0]
    assert t["opCode"] == 40 and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", t["acceptTime"])
    missing = client.get("/logistics/v1/trace")  # 缺必填参数：HTTP 仍是 200
    assert missing.status_code == 200 and missing.json()["success"] == "false"


def test_payment_style_integer_fen_epoch_ms_and_202(client):
    r = client.post("/payment/v1/refunds", json={"order_id": "XC1", "amount_fen": 25900, "reason": "x"}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 202
    body = r.json()
    assert isinstance(body["amount_fen"], int) and isinstance(body["created_at"], int) and body["created_at"] > 10**12
    pay = client.get("/payment/v1/orders/XC1/payments").json()["items"][0]
    assert pay["channel_display_name"] == "悦付" and isinstance(pay["paid_at"], int)
    err = client.get("/payment/v1/orders/XC1/payments", headers={"Authorization": "Bearer bad"}, auth=None)
    assert err.headers["content-type"].startswith("application/problem+json")


def test_member_style_camelcase_float_money_utc(client):
    body = client.get("/member/v1/users/U1/tier").json()
    assert body["ok"] is True and body["error"] is None
    r = body["result"]
    assert isinstance(r["spendYuan"], float) and r["assessedAt"].endswith("Z") and "nextAssessAt" in r
    assert client.get("/member/v1/users/lookup", params={"phoneLast4": "8213", "name": "陈"}).json()["result"]["candidates"]


def test_aftersale_style_and_ticket_style(client, internal_client):
    case = client.post(
        "/aftersale/v1/cases",
        json={"user_id": "U1", "order_id": "XC1", "items": [{"item_id": "I1"}], "case_type": "return_7day", "reason": "不想要"},
        headers={"Idempotency-Key": "k"},
    )
    assert case.status_code == 201 and case.json()["code"] == 0
    assert BEIJING_ISO.match(case.json()["data"]["applied_at"])
    t = client.get("/ticket/v1/tickets/T1").json()
    assert "ticket_id" in t and BEIJING_ISO.match(t["created_at"])
    pub = client.get("/ticket/v1/tickets/T1/messages").json()["items"]
    internal = internal_client.get("/ticket/v1/tickets/T1/messages").json()["items"]
    assert not any(m["is_internal"] for m in pub) and any(m["is_internal"] for m in internal)


def test_validation_error_uses_service_style_live(client):
    r = client.get("/order/v1/orders")  # 缺少必填 user_id
    assert r.status_code == 400 and r.json()["code"] == 40001 and r.json()["data"] is None
    r = client.get("/ticket/v1/tickets")
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "BAD_REQUEST"
