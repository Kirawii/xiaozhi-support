"""星川商城仿真业务环境（全部为虚构）。"""
import sys


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，统一改成 UTF-8（F7）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass  # 被 pytest 等替换成别的对象时忽略


_force_utf8()
