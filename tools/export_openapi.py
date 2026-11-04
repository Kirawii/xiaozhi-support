"""把 8 个服务的 OpenAPI 导出到 openapi/<service>.json。

用法：uv run python tools/export_openapi.py [输出目录]
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import xcsim  # noqa: F401  导入即强制 UTF-8 输出（F7）
from xcsim.simkit.config import SERVICES

ROOT = Path(__file__).resolve().parents[1]


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SERVICES:
        app = importlib.import_module(f"xcsim.services.{name}.app").create_app()
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
        print(f"导出 {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return written


if __name__ == "__main__":
    export(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "openapi")
