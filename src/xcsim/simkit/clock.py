"""本进程的仿真时钟。

默认冻结在锚点 N；running 模式按 rate 倍速随墙钟走动。
control 通过 /_sim/sync 把状态推给各服务，单进程模式下所有服务共用这一个对象。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .config import BJ_TZ, get_settings

BJ = ZoneInfo(BJ_TZ)


class SimClock:
    def __init__(self, anchor: datetime, wall: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._wall = wall
        self._mode = "frozen"
        self._rate = 1.0
        self._base_sim = anchor.astimezone(BJ)
        self._base_wall = wall()

    # 读
    def now(self) -> datetime:
        with self._lock:
            return self._now_locked()

    def _now_locked(self) -> datetime:
        if self._mode == "frozen":
            return self._base_sim
        elapsed = self._wall() - self._base_wall
        return self._base_sim + timedelta(seconds=elapsed * self._rate)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def rate(self) -> float:
        return self._rate

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"sim_time": self._now_locked().isoformat(), "mode": self._mode, "rate": self._rate}

    # 写
    def set(self, sim_time: datetime, mode: str | None = None, rate: float | None = None) -> None:
        if mode is not None and mode not in ("frozen", "running"):
            raise ValueError(f"mode 只能是 frozen/running: {mode}")
        if rate is not None and rate <= 0:
            raise ValueError("rate 必须大于 0")
        with self._lock:
            if mode is not None:
                self._mode = mode
            if rate is not None:
                self._rate = float(rate)
            self._base_sim = (sim_time if sim_time.tzinfo else sim_time.replace(tzinfo=BJ)).astimezone(BJ)
            self._base_wall = self._wall()

    def advance(self, seconds: float) -> datetime:
        self.set(self.now() + timedelta(seconds=seconds))
        return self.now()

    def apply(self, state: dict[str, Any]) -> None:
        """应用 control 推来的状态 {sim_time, mode, rate}。"""
        self.set(datetime.fromisoformat(state["sim_time"]), state.get("mode"), state.get("rate"))

    def reset(self) -> None:
        with self._lock:
            self._mode, self._rate = "frozen", 1.0
            self._base_sim = get_settings().anchor.astimezone(BJ)
            self._base_wall = self._wall()


clock = SimClock(get_settings().anchor)


def now() -> datetime:
    """仿真“现在”。业务代码必须用它，不要用系统时间。"""
    return clock.now()
