"""加载 policy.yaml。路径可用环境变量 XC_POLICY_PATH 覆盖（变异测试用）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from .models import Policy

# src/xcsim/policy/loader.py -> 仓库根目录
_REPO_ROOT = Path(__file__).resolve().parents[3]


def default_policy_path() -> Path:
    env = os.environ.get("XC_POLICY_PATH")
    if env:
        return Path(env)
    repo_file = _REPO_ROOT / "policy" / "policy.yaml"
    return repo_file if repo_file.exists() else Path.cwd() / "policy" / "policy.yaml"


def load_policy(path: str | os.PathLike[str] | None = None) -> Policy:
    """读取并校验，返回冻结模型（不缓存）。"""
    p = Path(path) if path else default_policy_path()
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Policy.model_validate(raw)


@lru_cache(maxsize=8)
def _cached(path: str, mtime_ns: int) -> Policy:
    return load_policy(path)


def get_policy() -> Policy:
    """带缓存的取数入口；环境变量或文件改动后自动重新加载。"""
    p = default_policy_path()
    return _cached(str(p), p.stat().st_mtime_ns)
