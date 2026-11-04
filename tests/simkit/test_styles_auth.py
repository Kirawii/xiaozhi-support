"""7 种错误风格 x 9 种状态；鉴权 401/403；通用响应头。"""
import pytest
from _support import SERVICES, Harness, expected, logical

from xcsim.simkit.errors import ApiError
from xcsim.simkit.styles import STYLES

STATUSES = [400, 401, 403, 404, 409, 422, 429, 503, 504]


@pytest.fixture()
def harness(tmp_path):
    cache: dict[str, Harness] = {}

    def get(service: str) -> Harness:
        if service not in cache:
            cache[service] = Harness(service, tmp_path)
        return cache[service]

    return get


# ------------------------------------------------------------ 响应工厂：任何风格都能表达 9 种状态
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("status", STATUSES)
def test_every_style_can_express_every_status(service, status):
    style = STYLES[service]
    resp = style.error_response(ApiError(status, message="出错了"), "req_1")
    import json

    body = json.loads(resp.body)
    # HTTP 状态：物流永远 200；会员的 404 用 200 + ok=false；其余必须是真实状态码
    if service == "logistics" or (service == "member" and status == 404):
        assert resp.status_code == 200
    else:
        assert resp.status_code == status
    # 都能在 body 里读出“这是个错误”以及我们给的信息
    assert "出错了" in json.dumps(body, ensure_ascii=False)


SHAPES = {
    "catalog": lambda b: set(b) == {"errorCode", "message"},
    "order": lambda b: set(b) == {"code", "message", "data", "request_id"} and b["data"] is None and b["request_id"] == "req_1",
    "aftersale": lambda b: set(b) == {"code", "message", "data", "request_id"},
    "logistics": lambda b: set(b) == {"success", "errCode", "errMsg", "data"} and b["success"] == "false",
    "payment": lambda b: {"type", "title", "status", "detail", "code", "instance"} <= set(b),
    "member": lambda b: set(b) == {"ok", "result", "error"} and b["ok"] is False and set(b["error"]) == {"code", "message"},
    "ticket": lambda b: set(b) == {"errors"} and set(b["errors"][0]) == {"code", "field", "message"},
    "control": lambda b: set(b) == {"errors"},
}


@pytest.mark.parametrize("service", SERVICES)
def test_error_body_shape_per_style(service):
    import json

    resp = STYLES[service].error_response(ApiError(404, message="找不到"), "req_1")
    assert SHAPES[service](json.loads(resp.body))


def test_payment_error_is_problem_json():
    resp = STYLES["payment"].error_response(ApiError(422, "REFUND_EXCEEDS_REFUNDABLE", "超额"), "r1")
    assert resp.media_type == "application/problem+json"
    import json

    body = json.loads(resp.body)
    assert body["status"] == 422 and body["title"] == "Unprocessable Entity"
    assert body["type"] == "https://errors.xingchuan.invalid/payment/refund-exceeds-refundable"


def test_aftersale_error_carries_rule_code_and_detail():
    import json

    err = ApiError(
        422, 42201, "窗口已过", rule_code="AS_WINDOW_EXPIRED",
        detail={"deadline_at": "2026-10-31T23:59:59+08:00", "policy_key": "return_7day.window_days", "policy_version": "3.0"},
    )
    body = json.loads(STYLES["aftersale"].error_response(err, "r").body)
    assert body["code"] == 42201 and body["rule_code"] == "AS_WINDOW_EXPIRED"
    assert body["detail"]["policy_key"] == "return_7day.window_days"


def test_custom_codes_pass_through_verbatim():
    import json

    assert json.loads(STYLES["catalog"].error_response(ApiError(404, "SPU_NOT_FOUND"), "r").body)["errorCode"] == "SPU_NOT_FOUND"
    assert json.loads(STYLES["logistics"].error_response(ApiError(404, "B0002"), "r").body)["errCode"] == "B0002"
    assert json.loads(STYLES["order"].error_response(ApiError(409, 40903), "r").body)["code"] == 40903


def test_success_envelopes():
    assert STYLES["catalog"].wrap({"a": 1}) == {"a": 1}
    assert STYLES["order"].wrap({"a": 1}, "r9") == {"code": 0, "message": "ok", "data": {"a": 1}, "request_id": "r9"}
    assert STYLES["logistics"].wrap({"a": 1}) == {"success": "true", "errCode": "0000", "errMsg": "", "data": {"a": 1}}
    assert STYLES["member"].wrap({"a": 1}) == {"ok": True, "result": {"a": 1}, "error": None}
    assert STYLES["payment"].wrap({"a": 1}) == {"a": 1}


# ------------------------------------------------------------ 鉴权
@pytest.mark.parametrize("service", SERVICES)
def test_missing_token_is_401(service, harness):
    h = harness(service)
    resp = h.client().get(h.p("/thing"), auth=None)
    assert logical(service, resp) == expected(service, 401)


@pytest.mark.parametrize("service", SERVICES)
def test_wrong_token_is_401(service, harness):
    h = harness(service)
    header = STYLES[service].auth_header
    value = "nope" if header != "Authorization" else "Bearer nope"
    resp = h.client().get(h.p("/thing"), headers={header: value}, auth=None)
    assert logical(service, resp) == expected(service, 401)


@pytest.mark.parametrize("service", SERVICES)
def test_valid_token_succeeds_with_headers(service, harness):
    h = harness(service)
    resp = h.client().get(h.p("/thing"))
    assert resp.status_code == 200
    assert resp.headers["X-Request-Id"].startswith("req_")
    assert resp.headers["X-Sim-Now"] == "2026-11-01T10:00:00+08:00"


@pytest.mark.parametrize("service", SERVICES)
def test_token_without_scope_is_403(service, harness):
    h = harness(service)
    # 业务服务：control token 没有业务 scope；control 服务：cs-agent token 没有 control scope
    wrong = "control" if service != "control" else "cs-agent"
    resp = h.client(wrong).get(h.p("/thing"))
    assert logical(service, resp) == expected(service, 403)


def test_internal_token_works_on_business_services_but_not_control_admin(harness):
    h = harness("order")
    assert h.client("internal").get(h.p("/thing")).status_code == 200
    c = harness("control")
    assert logical("control", c.client("internal").get(c.p("/thing"))) == expected("control", 403)


def test_logistics_requires_x_express_token_not_bearer(harness):
    from xcsim.simkit.config import get_settings

    h = harness("logistics")
    token = get_settings().tokens["cs-agent"]
    bearer = h.client().get(h.p("/thing"), headers={"Authorization": f"Bearer {token}"}, auth=None)
    assert logical("logistics", bearer) == expected("logistics", 401)
    ok = h.client().get(h.p("/thing"), headers={"X-Express-Token": token}, auth=None)
    assert ok.status_code == 200 and ok.json()["success"] == "true"


def test_bearer_services_ignore_x_express_token_and_reject_other_schemes(harness):
    from xcsim.simkit.config import get_settings

    h = harness("order")
    token = get_settings().tokens["cs-agent"]
    assert h.client().get(h.p("/thing"), headers={"X-Express-Token": token}, auth=None).status_code == 401
    assert h.client().get(h.p("/thing"), headers={"Authorization": f"Basic {token}"}, auth=None).status_code == 401
    assert h.client().get(h.p("/thing"), headers={"Authorization": "Bearer"}, auth=None).status_code == 401


def test_control_uses_x_control_token(harness):
    from xcsim.simkit.config import get_settings

    h = harness("control")
    token = get_settings().tokens["control"]
    assert h.client().get(h.p("/thing"), headers={"X-Control-Token": token}, auth=None).status_code == 200
    assert h.client().get(h.p("/thing"), headers={"Authorization": f"Bearer {token}"}, auth=None).status_code == 401


# ------------------------------------------------------------ 兜底错误也走各自风格
@pytest.mark.parametrize("service", SERVICES)
def test_unknown_path_and_bad_input_use_service_style(service, harness):
    h = harness(service)
    c = h.client()
    assert logical(service, c.get(f"/{service}/v1/no-such-path"))[1] is not None
    # 缺请求体 -> 参数校验失败，统一 400
    resp = c.post(h.p("/notes"), headers={"Idempotency-Key": "k"}, content=b"not json", )
    assert logical(service, resp)[1] is not None
    if service != "logistics":
        assert resp.status_code == 400


def test_x_request_id_is_echoed_when_supplied(harness):
    h = harness("catalog")
    resp = h.client().get(h.p("/thing"), headers={"X-Request-Id": "trace-abc"})
    assert resp.headers["X-Request-Id"] == "trace-abc"

