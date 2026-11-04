"""xcsim 命令行。目前只是占位框架：gen/reset 由 WP2 填充，serve/report 由 WP8 填充。"""
from __future__ import annotations

import argparse
import sys

COMMANDS = {
    "gen": "生成数据与场景（WP2 实现）",
    "serve": "启动单进程服务（WP8 实现）",
    "reset": "从快照重置全部数据库（WP8 实现）",
    "report": "输出逼真度报告（WP8 实现）",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xcsim", description="星川商城仿真业务环境")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"xcsim {args.command}: 未实现（{COMMANDS[args.command]}）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
