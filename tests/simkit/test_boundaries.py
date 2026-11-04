"""边界守卫：服务之间不得互相 import，simkit/policy 不得反向依赖具体服务。"""
import re
from pathlib import Path

import pytest

from xcsim.simkit.config import SERVICES

SRC = Path(__file__).resolve().parents[2] / "src" / "xcsim"
IMPORT = re.compile(r"^\s*(?:from|import)\s+xcsim\.services\.(\w+)", re.M)
RELATIVE_UP = re.compile(r"^\s*from\s+\.\.", re.M)


@pytest.mark.parametrize("service", SERVICES)
def test_service_does_not_import_other_services(service):
    for f in (SRC / "services" / service).rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert set(IMPORT.findall(text)) <= {service}, f
        assert not RELATIVE_UP.search(text), f"{f} 用相对导入跨出了服务目录"


@pytest.mark.parametrize("shared", ["simkit", "policy"])
def test_shared_packages_do_not_depend_on_services(shared):
    for f in (SRC / shared).rglob("*.py"):
        assert "xcsim.services" not in f.read_text(encoding="utf-8"), f


def test_policy_does_not_depend_on_simkit():
    for f in (SRC / "policy").rglob("*.py"):
        assert "simkit" not in f.read_text(encoding="utf-8"), f
