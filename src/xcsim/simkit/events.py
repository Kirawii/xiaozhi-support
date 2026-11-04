"""事件：上报（service -> control）与接收（control -> service，按 seq 去重）。

事件结构（§6.5）：{seq, sim_time, type, service, entity_type, entity_id, payload, cause_seq}
seq 由 control 分配，业务服务上报时不带 seq。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from . import clock, fmt
from .client import DependencyError, ServiceClient

log = logging.getLogger("xcsim.events")

REPORT_PATH = "/control/_sim/report"


class EventIn(BaseModel):
    """业务服务上报给 control 的事件（尚无 seq）。"""

    type: str
    service: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    cause_seq: int | None = None
    sim_time: str | None = None


class EventOut(EventIn):
    """control 分配 seq 之后的完整事件。"""

    seq: int
    sim_time: str


class EventReporter:
    """上报客户端。上报失败只记日志，不影响业务请求。"""

    def __init__(self, service: str, client: ServiceClient | None = None) -> None:
        self.service = service
        self._client = client

    @property
    def client(self) -> ServiceClient:
        if self._client is None:
            self._client = ServiceClient("control")
        return self._client

    def report(
        self,
        type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
        cause_seq: int | None = None,
    ) -> dict[str, Any] | None:
        event = EventIn(
            type=type, service=self.service, entity_type=entity_type, entity_id=entity_id,
            payload=payload or {}, cause_seq=cause_seq, sim_time=fmt.iso_bj(clock.now()),
        )
        try:
            resp = self.client.post(REPORT_PATH, json=event.model_dump())
            return resp.json() if resp.status_code < 300 else None
        except (DependencyError, ValueError) as exc:
            log.warning("事件上报失败 %s: %s", type, exc)
            return None


class EventInbox:
    """接收 control 分发的事件；同一个 seq 只处理一次。"""

    def __init__(self) -> None:
        self._seen: set[int] = set()
        self._last_seq = 0
        self._lock = threading.Lock()
        self.handler: Callable[[dict[str, Any]], None] | None = None

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def receive(self, events: list[dict[str, Any]]) -> dict[str, int]:
        accepted = duplicates = 0
        for ev in sorted(events, key=lambda e: e["seq"]):
            with self._lock:
                if ev["seq"] in self._seen:
                    duplicates += 1
                    continue
                self._seen.add(ev["seq"])
                self._last_seq = max(self._last_seq, ev["seq"])
            if self.handler is not None:
                self.handler(ev)
            accepted += 1
        return {"accepted": accepted, "duplicates": duplicates, "last_seq": self._last_seq}

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()
            self._last_seq = 0
