# 星川商城仿真业务环境（简化版）

虚构电商“星川商城”的业务中台。你要写的客服 Agent 通过这里的 HTTP 接口查订单、查物流、办售后、发起退款、转人工，并用 `kb-bench/` 里的知识库回答规则问题。

所有公司、商品、人员、电话、快递公司都是虚构的。

## 快速开始

```bash
uv sync
uv run xcsim serve            # http://127.0.0.1:8000 ，接口文档在 /docs
```

试一下：

```bash
curl http://127.0.0.1:8000/api/v1/time -H "Authorization: Bearer agent-dev-token"
```

如果本机设置了 HTTP 代理，访问 127.0.0.1 时要绕过代理（curl 加 `--noproxy '*'`，httpx 用 `trust_env=False`）。

## 两件必须知道的事

1. **“今天”以仿真时钟为准，不是你电脑的时间。** 默认是 2026-11-01 10:00（周日）。Agent 判断“还能不能退”之前，先调 `GET /api/v1/time`。每个响应头里也有 `X-Sim-Now`。
2. **接口不认识“用户”，只认识服务令牌。** 查询要带 `user_id`。确认来访者是谁、不把 A 的订单告诉 B，是 Agent 的责任。

## 鉴权

| 用途 | 请求头 | 默认值（可用环境变量改） |
|---|---|---|
| 业务接口（给 Agent 用） | `Authorization: Bearer <token>` | `agent-dev-token`（`XC_AGENT_TOKEN`） |
| 控制接口（只给测试和评测用） | `X-Control-Token: <token>` | `control-dev-token`（`XC_CONTROL_TOKEN`） |

**不要把控制令牌交给 Agent。**

## 业务接口

成功响应为 `{"code": 0, "message": "ok", "data": ...}`。

失败时返回 HTTP 4xx/5xx，响应体为 `{"code": "错误码", "message": "说明", "detail": {...}}`。

金额是字符串，单位元（如 `"99.00"`）；时间是带 `+08:00` 的 ISO 格式。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/v1/time` | 仿真当前时间、人工客服是否在线 |
| `GET /api/v1/users/lookup?phone=` 或 `?phone_last4=&name=` | 核验身份、查找用户 |
| `GET /api/v1/users/{user_id}` | 会员等级、积分、默认地址 |
| `GET /api/v1/products?q=`、`GET /api/v1/products/{spu_id}` | 商品、现价、价格类型、详情页售后说明 |
| `GET /api/v1/orders?user_id=` | 订单列表 |
| `GET /api/v1/orders/{order_id}?user_id=` | 订单详情：明细、优惠分摊、运费拆解、包裹 |
| `POST /api/v1/orders/{order_id}/cancel` | 取消订单（未发货） |
| `POST /api/v1/orders/{order_id}/address` | 修改地址（未发货，限一次） |
| `GET /api/v1/logistics/{mail_no}` | 物流轨迹，**第三方风格**（见下文） |
| `POST /api/v1/aftersales` | 售后申请，**服务端强校验规则** |
| `GET /api/v1/aftersales?user_id=` | 售后单列表 |
| `POST /api/v1/aftersales/{case_id}/withdraw` | 撤销（仍计入申请次数） |
| `POST /api/v1/aftersales/{case_id}/return-shipment` | 登记寄回单号；金星会员满足条件时自动先行退款 |
| `POST /api/v1/refunds` | 发起退款，**必须带 `Idempotency-Key` 头**，异步处理 |
| `GET /api/v1/refunds?order_id=` 或 `?idempotency_key=` | 查询退款，超时后对账用 |
| `GET /api/v1/refunds/{refund_id}` | 退款详情、预计到账时间 |
| `POST /api/v1/price-protections` | 价格保护，补差价到星川余额 |
| `POST /api/v1/tickets`、`GET /api/v1/tickets/{id}` | 转人工工单 |

### 物流接口的风格不一样

物流接口模拟外部快递公司：

- HTTP 状态**永远是 200**，要看 `success` 字段，它是字符串 `"true"`/`"false"`。
- 字段是驼峰命名。
- 时间是 `"2026-10-25 14:03:22"` 这样的北京时间字符串。
- 状态码是数字 `opCode`：10 揽收、20 运输中、30 派送中、35 入柜或驿站、40 签收、50 异常、60 退回中。
- 轨迹偶尔会有重复扫描记录。

### 售后类型

`POST /api/v1/aftersales` 的 `type` 可选：

- `return_7day`：七天无理由退货
- `size_exchange`：换码，需带 `target_sku_id`
- `quality_refund`、`quality_exchange`、`quality_repair`：质量问题退款、换货、维修
- `wrong_missing`：错发漏发
- `fresh_claim`：生鲜问题，需带 `bad_ratio`，如 `"0.2"`

不符合规则时返回 422，`code` 是规则码，例如：

- `AS_WINDOW_EXPIRED`：超过无理由期限
- `AS_APPLY_LIMIT`：申请次数用完
- `AS_OPENED`、`AS_ACTIVATED`：已拆封、已激活
- `AS_DETAIL_OVERRIDE`：详情页另有规定
- `AS_OPTION_NOT_AVAILABLE`：当前时段不支持该处理方式
- `AS_FRESH_CLAIM_EXPIRED`：生鲜超过 24 小时

**没有“预检”接口。** Agent 应该先依据知识库和订单数据自己判断，再提交。

### 退款是异步的

- 提交后先返回 `processing`；超过 2000 元返回 `reviewing`，需要人工复核 1 个工作日。
- 默认约 5 秒后（`XC_AUTO_SETTLE_SECONDS`），或仿真时钟走过到期时间后，状态变为 `succeeded` 或 `failed`。
- 网络超时不代表失败。先用同一个 `Idempotency-Key` 重试，或按 `idempotency_key` 查询；**换一个新键重试会被拒绝（409）**，避免重复退款。

## 控制接口（测试和评测用）

| 方法与路径 | 作用 |
|---|---|
| `POST /control/reset` | 重置全部数据、时钟、故障；可带 `{"seed": 42}` |
| `GET/POST /control/clock` | 读时钟；`{"set": "2026-11-02T09:00:00+08:00"}` 或 `{"advance_minutes": 60}` 时间旅行 |
| `GET/POST/DELETE /control/faults` | 故障注入：`{"path": "/api/v1/logistics", "kind": "http_503", "times": 0}` |
| `GET /control/scenarios` | 全部命名场景 |
| `POST /control/scenarios/{id}/prepare` | 一键准备场景：重置、拨钟、注入故障 |
| `POST /control/refunds/{id}/settle` | 让退款立即成功或失败：`{"result": "failed", "failure_code": "CARD_CANCELLED"}` |
| `POST /control/parcels/{mail_no}/deliver` | 让包裹立即签收 |

故障类型：

- `latency`：配合 `latency_ms`
- `http_429`
- `http_503`
- `timeout`：配合 `hold_ms`，挂起后返回 504
- `timeout_after_commit`：**请求已经生效**，但客户端收到 504

`times` 为 0 表示一直生效。

## 命名场景：评测你的 Agent

`scenarios.yaml` 有 66 个场景，覆盖：

- 七天无理由的边界（第 7 天 23:30、驿站入站时间、申请次数、拆封或激活、详情页优先）
- 退款金额分摊、换码
- 质量问题分档、错发 48 小时、检测报告门槛
- 生鲜 24 小时与坏果率
- 运费边界（99 元、偏远 199 元、大件、金星）
- 退款（2000 元复核、金星 500 元先行退款、失败、超时但已成功、重复申请）
- 价保、物流异常、取消与改地址、新旧规则过渡、转人工、越权查询、服务故障

每个场景有专属用户和订单，包括：

- `opening_utterance`：用户开口的第一句话
- `expected`：期望的正确处理
- `check`：一个可自动核验的接口调用

评测一个场景的流程：

1. `POST /control/scenarios/RET-05/prepare`
2. 让你的 Agent 以 `user_id` 身份接待 `opening_utterance`
3. 对照 `expected` 评判回答，必要时用接口检查数据状态

重新导出场景：`uv run xcsim scenarios`。

## 知识库

`kb-bench/knowledge_base/` 有 20 篇文档（12 篇政策、7 篇商品说明、1 篇常见问题），另有 100 道带自动评分的问答题（`kb-bench/benchmark/`）。用法见 `kb-bench/README.md`。

- 校验：`cd kb-bench && uv run python scripts/bench.py validate`
- 接口的规则校验读 `policy/policy.yaml`，它的数字按这套知识库整理，两边一致；改规则时两处要同步修改。

## 数据规模

- 普通用户 300 个、普通订单约 1,500 单，覆盖最近 18 个月，含大促波动、拆单、赠品、分期、组合优惠和物流异常。
- 另有 66 个场景各自的专属用户和订单。
- 同一种子生成的数据完全相同。

## 测试

```bash
uv run pytest tests -q
```

## 现状与后续

这是**简化版**：单进程、内存数据、统一的接口风格（物流除外）。

`DESIGN.md` 是完整版设计，包括：

- 7 个独立服务、各自的数据库和不同的接口风格
- 95 个场景、80 篇知识库
- 对话数据集、意图评测样本、用户画像

`src/xcsim/services/` 下是完整版的接口桩，尚未实现。简化版不依赖它们。
