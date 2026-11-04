"""故障规则（§6.2）、故障类型（§6.3）、realistic 配置档。

概率判断用“规则自己的命名随机子流”：``Random(sha256(seed|fault|service|rule_id))``，
同种子、同请求序列就得到同样的故障序列。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, model_validator

FaultKind = Literal["latency", "http_429", "http_503", "timeout", "timeout_after_commit", "corrupt"]
CorruptMode = Literal[
    "drop_field", "null_field", "wrong_type", "stale_status", "duplicate_item", "truncated_json", "garbled_encoding"
]
_NEEDS_FIELD = {"drop_field", "null_field", "wrong_type", "duplicate_item"}


class FaultRule(BaseModel):
    """与 control 的 /faults 请求体一致。"""

    id: str
    service: str = "*"
    method: str = "*"
    path: str = "*"  # 精确路径；可用 {param} 通配一段、* 通配任意
    kind: FaultKind
    probability: float = 1.0
    times: int | None = None  # 最多触发几次；None 不限
    hold_ms: int = 35000  # timeout / timeout_after_commit 挂起多久
    latency_ms: int = 0
    jitter_ms: int = 0
    retry_after_s: int = 1  # http_429 的 Retry-After
    mode: CorruptMode | None = None
    field: str | None = None  # corrupt 的目标字段，点号分隔，如 data.signTime
    caller: str | None = None  # cs-agent / internal
    match_body: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> "FaultRule":
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability 必须在 0~1 之间")
        if self.kind == "corrupt":
            if self.mode is None:
                raise ValueError("corrupt 必须指定 mode")
            if self.mode in _NEEDS_FIELD and not self.field:
                raise ValueError(f"mode={self.mode} 必须指定 field")
        return self


# ------------------------------------------------------------------ 数据损坏
_STALE_STATUS = {
    "completed": "delivered", "delivered": "shipped", "shipped": "paid", "paid": "pending_payment",
    "succeeded": "processing", "processing": "reviewing", "refunded": "refund_pending",
    "approved": "submitted", "resolved": "assigned", "assigned": "queued",
}


def _walk(node: Any, parts: list[str], action: Callable[[Any, str], None]) -> None:
    """沿路径走到倒数第二层，对目标键执行 action；遇到列表且下一段不是下标时对每个元素执行。"""
    if isinstance(node, list):
        if parts and parts[0].isdigit():
            idx = int(parts[0])
            if idx < len(node):
                if len(parts) == 1:
                    action(node, parts[0])
                else:
                    _walk(node[idx], parts[1:], action)
            return
        for item in node:
            _walk(item, parts, action)
        return
    if not isinstance(node, dict) or not parts:
        return
    if len(parts) == 1:
        if parts[0] in node:
            action(node, parts[0])
        return
    if parts[0] in node:
        _walk(node[parts[0]], parts[1:], action)


def _wrong_type(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return 0
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return [value]
    return "unexpected"


def corrupt_body(raw: bytes, mode: str, field_path: str | None) -> bytes:
    """按 mode 破坏一个 JSON 响应体。解析不了的原样返回。"""
    if mode == "truncated_json":
        return raw[: max(1, int(len(raw) * 0.6))]
    try:
        doc = json.loads(raw)
    except ValueError:
        return raw
    if mode == "garbled_encoding":
        # UTF-8 字节被按 latin-1 解读的典型乱码
        text = json.dumps(doc, ensure_ascii=False)
        return text.encode("utf-8").decode("latin-1").encode("utf-8")
    parts = (field_path or "status").split(".")

    def drop(container, key):
        if isinstance(container, list):
            container.pop(int(key))
        else:
            del container[key]

    def null(container, key):
        container[int(key) if isinstance(container, list) else key] = None

    def wrong(container, key):
        k = int(key) if isinstance(container, list) else key
        container[k] = _wrong_type(container[k])

    def stale(container, key):
        value = container[key]
        container[key] = _STALE_STATUS.get(value, "pending") if isinstance(value, str) else value

    def dup(container, key):
        target = container[int(key) if isinstance(container, list) else key]
        if isinstance(target, list) and target:
            target.insert(1, json.loads(json.dumps(target[0])))

    action = {
        "drop_field": drop, "null_field": null, "wrong_type": wrong, "stale_status": stale, "duplicate_item": dup,
    }[mode]
    _walk(doc, parts, action)
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------------ realistic 配置档
@dataclass
class RealisticConfig:
    median_ms: float = 80.0  # P50 约 80ms
    p95_ms: float = 600.0  # P95 约 600ms
    max_ms: float = 5000.0
    error_503_rate: float = 0.005
    bucket_capacity: float = 30.0  # 令牌桶：每个 token 的突发容量
    bucket_refill_per_s: float = 15.0
    service_multiplier: dict[str, float] = field(default_factory=lambda: {"logistics": 3.0})
    unlimited_callers: tuple[str, ...] = ("internal",)  # 服务间调用不限流


class TokenBucket:
    def __init__(self, capacity: float, refill_per_s: float, now: Callable[[], float] = time.monotonic) -> None:
        self.capacity, self.refill, self._now = capacity, refill_per_s, now
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """取一个令牌。成功返回 0；否则返回还需等待的秒数。"""
        with self._lock:
            t = self._now()
            tokens, last = self._state.get(key, (self.capacity, t))
            tokens = min(self.capacity, tokens + (t - last) * self.refill)
            if tokens >= 1:
                self._state[key] = (tokens - 1, t)
                return 0.0
            self._state[key] = (tokens, t)
            return (1 - tokens) / self.refill


# ------------------------------------------------------------------ 规则注册表
@dataclass
class Decision:
    delay_s: float = 0.0
    kind: str | None = None
    rule: FaultRule | None = None
    retry_after_s: int = 1


def _rng(seed: int, *parts: str) -> random.Random:
    digest = hashlib.sha256("|".join([str(seed), *parts]).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _path_regex(pattern: str) -> re.Pattern[str]:
    out = re.escape(pattern).replace(r"\*", ".*")
    out = re.sub(r"\\\{[^/}]*\\\}", "[^/]+", out)
    return re.compile(out)


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    return expected == actual


class _Armed:
    def __init__(self, rule: FaultRule, seed: int, service: str) -> None:
        self.rule = rule
        self.service = service
        self.remaining = rule.times
        self.rng = _rng(seed, "fault", service, rule.id)
        self.regex = _path_regex(rule.path)

    def matches(self, method: str, path: str, caller: str | None, body: Any) -> bool:
        r = self.rule
        if r.service not in ("*", self.service):
            return False
        if r.method != "*" and r.method.upper() != method.upper():
            return False
        if r.path != "*" and not self.regex.fullmatch(path):
            return False
        if r.caller and r.caller != caller:
            return False
        if r.match_body is not None and not _subset(r.match_body, body):
            return False
        return True


class FaultRegistry:
    def __init__(self, service: str, seed: int = 42) -> None:
        self.service = service
        self.seed = seed
        self.profile = "clean"
        self.realistic = RealisticConfig()
        self._armed: list[_Armed] = []
        self._lock = threading.Lock()
        self._bucket = TokenBucket(self.realistic.bucket_capacity, self.realistic.bucket_refill_per_s)
        self._rng_latency = _rng(seed, "realistic", service, "latency")
        self._rng_503 = _rng(seed, "realistic", service, "503")

    # ---- 管理
    def rules(self) -> list[FaultRule]:
        return [a.rule for a in self._armed]

    def replace(self, rules: list[FaultRule], seed: int | None = None) -> None:
        """整体替换规则。定义没变的规则保留剩余次数和随机流进度，避免重复同步把 times 重置。"""
        with self._lock:
            if seed is not None and seed != self.seed:
                self.seed = seed
                self._rng_latency = _rng(seed, "realistic", self.service, "latency")
                self._rng_503 = _rng(seed, "realistic", self.service, "503")
                self._armed = []
            old = {a.rule.id: a for a in self._armed}
            self._armed = [
                old[r.id] if r.id in old and old[r.id].rule == r else _Armed(r, self.seed, self.service)
                for r in rules
            ]

    def add(self, rule: FaultRule) -> None:
        self.replace([*self.rules(), rule])

    def remove(self, rule_id: str) -> bool:
        before = len(self._armed)
        self.replace([r for r in self.rules() if r.id != rule_id])
        return len(self._armed) != before

    def clear(self) -> None:
        with self._lock:
            self._armed = []

    def set_profile(self, profile: str) -> None:
        if profile not in ("clean", "realistic"):
            raise ValueError("profile 只能是 clean / realistic")
        self.profile = profile

    def reset_state(self) -> None:
        """reset：清规则、回到 clean、重置随机流和令牌桶。"""
        with self._lock:
            self._armed = []
            self.profile = "clean"
            self._bucket = TokenBucket(self.realistic.bucket_capacity, self.realistic.bucket_refill_per_s)
            self._rng_latency = _rng(self.seed, "realistic", self.service, "latency")
            self._rng_503 = _rng(self.seed, "realistic", self.service, "503")

    # ---- 判定
    def _latency_ms(self) -> float:
        cfg = self.realistic
        if cfg.median_ms <= 0:
            return 0.0
        sigma = math.log(cfg.p95_ms / cfg.median_ms) / 1.6449  # 对数正态：P50=median，P95=p95
        value = cfg.median_ms * math.exp(sigma * self._rng_latency.gauss(0, 1))
        return min(value, cfg.max_ms) * cfg.service_multiplier.get(self.service, 1.0)

    def evaluate(self, method: str, path: str, caller: str | None, body: Any) -> Decision:
        with self._lock:
            d = Decision()
            if self.profile == "realistic":
                cfg = self.realistic
                if caller not in cfg.unlimited_callers:
                    wait = self._bucket.take(caller or "anonymous")
                    if wait > 0:
                        return Decision(kind="http_429", retry_after_s=max(1, math.ceil(wait)))
                d.delay_s += self._latency_ms() / 1000
            for armed in self._armed:
                if armed.remaining == 0 or not armed.matches(method, path, caller, body):
                    continue
                if armed.rng.random() >= armed.rule.probability:
                    continue
                if armed.remaining is not None:
                    armed.remaining -= 1
                rule = armed.rule
                if rule.kind == "latency":
                    jitter = armed.rng.uniform(-rule.jitter_ms, rule.jitter_ms) if rule.jitter_ms else 0.0
                    d.delay_s += max(rule.latency_ms + jitter, 0.0) / 1000
                    continue
                d.kind, d.rule, d.retry_after_s = rule.kind, rule, rule.retry_after_s
                return d
            if self.profile == "realistic" and self._rng_503.random() < self.realistic.error_503_rate:
                d.kind = "http_503"
            return d
