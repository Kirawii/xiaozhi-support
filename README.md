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

金额是字符串，单位元（如 `"99.00"`）；时间是带 `+08:00` 的 ISO 格式；订单号格式为 `ORD-20261020-0001`。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/v1/time` | 仿真当前时间、人工客服是否在线 |
| `POST /api/v1/identity/verify` | 身份核验：`{order_id, phone_last4}`，返回 `verified` 和 `user_id`（对应教程的 `verify_identity`） |
| `GET /api/v1/users/lookup?phone=` 或 `?phone_last4=&name=` | 查找用户 |
| `GET /api/v1/users/{user_id}` | 会员等级、积分、默认地址 |
| `GET /api/v1/products?q=`、`GET /api/v1/products/{spu_id}` | 商品、现价、价格类型、详情页售后说明 |
| `GET /api/v1/orders?user_id=` | 订单列表 |
| `GET /api/v1/orders/{order_id}?user_id=` | 订单详情：明细、优惠分摊、运费拆解、包裹 |
| `POST /api/v1/orders/{order_id}/cancel` | 取消订单（未发货） |
| `POST /api/v1/orders/{order_id}/address` | 修改地址（未发货，限一次） |
| `GET /api/v1/logistics/{mail_no}` | 物流轨迹，**第三方风格**（见下文） |
| `POST /api/v1/aftersales` | 售后申请，**服务端强校验规则**；body 加 `"dry_run": true` 只校验和试算、不创建 |
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

**预检（`dry_run`）**：用于教程阶段 4 的两阶段确认。先带 `"dry_run": true` 调用，规则不通过返回同样的 422；通过则返回试算结果（退款金额、截止时间、是否需要人工复核），不写入任何数据。用户确认后再去掉 `dry_run` 正式提交。预检只是“试一下”，规则判断和向用户解释仍然是 Agent 的工作。

**幂等键**：售后、取消订单、修改地址可带 `Idempotency-Key` 头，同键同参返回第一次的结果（响应头 `Idempotent-Replayed: true`），同键异参返回 422；退款**必须**带。

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

`scenarios.yaml` 有 78 个场景，格式对应教程阶段 12。每个场景有专属用户和订单：

| 字段 | 含义 |
|---|---|
| `opening_utterance` | 用户开口的第一句话 |
| `user_profile` | 给用户模拟器的人设 `persona`、已知信息 `knows`（订单号、手机尾号、姓名）、目标 `goal`、只在被问到时才说的 `hidden_rules` |
| `expected.summary` | 期望的正确处理 |
| `expected.must_mention` | 回复中必须出现的关键信息 |
| `expected.forbidden_tools` | 不该调用的工具（按教程的工具名，如 `create_return_request`） |
| `expected.db_assertions` | 对话结束后用接口核验数据状态，例如“这个订单不应产生售后单”：`{call, path, op: len_eq / eq, value}` |
| `tutorial_category` | 教程的场景分类：正常办理、规则边界、信息不全或模糊、多诉求、对抗、应转人工，另有依赖故障（阶段 16） |
| `check` | 环境自测用的参考调用（相当于“标准答案”的一步），不要给 Agent 看 |

评测一个场景：`POST /control/scenarios/RET-05/prepare` → 用户模拟器按 `user_profile` 与你的 Agent 对话 → 按 `expected` 打分（数据库断言 + 必须告知 + 禁用工具）。

重新导出场景：`uv run xcsim scenarios`。

## 与教程《从零构建工业级 Agent 客服系统》的对应

本环境扮演教程里“业务中台”的角色：订单、退款、物流等业务数据和规则校验由它提供，你的 Agent 通过 HTTP 调用。

| 教程阶段 | 怎么用本环境 |
|---|---|
| 阶段 2 业务领域与数据层 | 不再自己建业务表和种子数据。`services/` 写成调用本环境的客户端（防腐层）：把 HTTP 错误码转换成你的业务异常（如 `AS_WINDOW_EXPIRED` → `ReturnWindowExpired`），把元字符串转成整数分。规则仍要读懂并写进 `docs/policy.md`：提示词和评测都要用。幂等键、注入时钟（读 `/api/v1/time`）照样要做 |
| 阶段 4 工具 | 教程的 9 个工具对应：`verify_identity` → `POST /identity/verify`；`list_recent_orders`、`get_order` → 订单接口；`get_shipment` → 物流接口（注意第三方风格）；`cancel_order`、`update_shipping_address`、`create_return_request` → 对应写接口（两阶段确认用 `dry_run` 预检）；`transfer_to_human` → 工单接口 |
| 阶段 7 护栏 | 身份核验用订单号 + 手机尾号；场景里有同名不同人、用他人订单号核验、冒充主管等对抗用例 |
| 阶段 8 RAG | 用 `kb-bench/`：`bench.py template/score/judge --split dev`，最终在 `--split test` 跑一次 |
| 阶段 12 评测 | 用 `scenarios.yaml` 和控制接口；知识问答稳定性用 `bench.py passk --pred run1.jsonl run2.jsonl ...` |
| 阶段 13 部署 | 把 `docker-compose.yml` 里的 `xingchuan` 服务加到你的 compose 中，你的服务用 `http://xingchuan:8000` 访问 |
| 阶段 16 失败处理 | 用故障注入：退款 `timeout_after_commit`（超时但已成功）、物流持续 503、订单接口 429，正好对应教程的“结果不确定的写操作”和熔断降级 |

## 知识库

`kb-bench/knowledge_base/` 有 20 篇文档（12 篇政策、7 篇商品说明、1 篇常见问题），另有 100 道带自动评分的问答题（`kb-bench/benchmark/`）。用法见 `kb-bench/README.md`。

- 校验：`cd kb-bench && uv run python scripts/bench.py validate`
- 接口的规则校验读 `policy/policy.yaml`，它的数字按这套知识库整理，两边一致；改规则时两处要同步修改。

## 数据规模

- 普通用户 300 个、普通订单约 1,500 单，覆盖最近 18 个月，含大促波动、拆单、赠品、分期、组合优惠和物流异常。
- 另有 78 个场景各自的专属用户和订单。
- 同一种子生成的数据完全相同。

## Docker

```bash
docker compose up
```

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
