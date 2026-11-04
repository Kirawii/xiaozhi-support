"""公共夹具。

- 每个测试前重置仿真时钟、清掉可能残留的 XC_POLICY_PATH；
- 会话级活体夹具：在后台线程里用 uvicorn 启动单进程组合应用（随机空闲端口），
  提供已带 token 的 httpx 客户端。只有用到它的测试才会真正启动服务。
"""
from __future__ import annotations

import os
import socket
import threading
import time

import httpx
import pytest

os.environ.setdefault("XC_AUTO_SETTLE_SECONDS", "0")  # 测试里关闭墙钟自动推进
os.environ["NO_PROXY"] = "127.0.0.1,localhost"  # 本机回环不走系统代理


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    from xcsim.simkit import clock

    monkeypatch.delenv("XC_POLICY_PATH", raising=False)
    clock.clock.reset()
    yield
    clock.clock.reset()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    """单进程组合应用，监听 127.0.0.1 的随机端口。返回基地址。"""
    import uvicorn

    from xcsim.combined import create_app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("测试服务启动失败")
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    old = os.environ.get("XC_BASE_URL")
    os.environ["XC_BASE_URL"] = base  # 让 ServiceClient 指向这个服务
    yield base
    server.should_exit = True
    thread.join(timeout=10)
    if old is None:
        os.environ.pop("XC_BASE_URL", None)
    else:
        os.environ["XC_BASE_URL"] = old


def _client(base: str, kind: str) -> httpx.Client:
    from xcsim.simkit.client import TokenAuth
    from xcsim.simkit.config import get_settings

    return httpx.Client(base_url=base, auth=TokenAuth(get_settings().tokens[kind]), timeout=15, trust_env=False, verify=False)  # http 明文，verify=False 省掉加载证书的耗时


@pytest.fixture(scope="session")
def client(live_server):
    """cs-agent token（学习者视角）。按路径自动选择 Bearer / X-Express-Token。"""
    with _client(live_server, "cs-agent") as c:
        yield c


@pytest.fixture(scope="session")
def internal_client(live_server):
    with _client(live_server, "internal") as c:
        yield c


@pytest.fixture(scope="session")
def control_client(live_server):
    with _client(live_server, "control") as c:
        yield c


@pytest.fixture(scope="session")
def anon_client(live_server):
    """不带任何 token 的客户端。"""
    with httpx.Client(base_url=live_server, timeout=15, trust_env=False, verify=False) as c:
        yield c
