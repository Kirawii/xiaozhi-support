"""xcsim 命令行。

    xcsim serve [--port 8000]          启动仿真业务接口（单进程）
    xcsim scenarios [--out scenarios.yaml]   导出命名场景
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("xcsim.lite.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_scenarios(args) -> int:
    import yaml

    from xcsim.lite.world import World, iso

    w = World.build(background=False)
    items = []
    for s in w.scenarios.values():
        items.append({k: (iso(v) if k == "clock" else v) for k, v in s.items()})
    header = ("# 星川商城命名场景（由 `xcsim scenarios` 生成，勿手改）\n"
              "# 用法：POST /control/scenarios/{id}/prepare 后，以 user_id 身份和 Agent 对话；\n"
              "# check 是一个可自动核验的接口调用及期望，expected 是期望的正确处理。\n")
    Path(args.out).write_text(header + yaml.safe_dump({"scenarios": items}, allow_unicode=True, sort_keys=False),
                              encoding="utf-8")
    print(f"已写出 {len(items)} 个场景到 {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = argparse.ArgumentParser(prog="xcsim", description="星川商城仿真业务环境")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="启动仿真业务接口")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("scenarios", help="导出命名场景到 YAML")
    p.add_argument("--out", default="scenarios.yaml")
    p.set_defaults(fn=cmd_scenarios)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
