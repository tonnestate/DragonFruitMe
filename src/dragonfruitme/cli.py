from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import DragonFruitMe
from .policy import FetchPolicy


def _json(value: str):
    return json.loads(value)


def _source(args: argparse.Namespace) -> dict:
    if args.html_file:
        return {"html": Path(args.html_file).read_text(encoding="utf-8", errors="replace"), "base_url": args.base_url}
    return {"url": args.url}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dragonfruitme", description="Escalating web-to-graph extraction for AI agents")
    parser.add_argument("--state-dir", help="recipe store directory (default: $DRAGONFRUITME_STATE_DIR or ~/.dragonfruitme)")
    parser.add_argument("--ignore-robots", action="store_true", help="host decision: do not consult robots.txt")
    parser.add_argument("--min-interval", type=float, help="seconds between requests to the same host")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_source(p: argparse.ArgumentParser) -> None:
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument("--url")
        group.add_argument("--html-file", help="read HTML from a local file instead of fetching")
        p.add_argument("--base-url", help="base URL for --html-file (link resolution and recipe scope)")

    p_observe = sub.add_parser("observe", help="page graph overview")
    add_source(p_observe)

    p_locate = sub.add_parser("locate", help="relevance search over the page graph")
    add_source(p_locate)
    p_locate.add_argument("--query", required=True)
    p_locate.add_argument("--max-results", type=int, default=8)

    p_extract = sub.add_parser("extract", help="escalating per-field extraction")
    add_source(p_extract)
    p_extract.add_argument("--fields-json", type=_json, required=True,
                           help='e.g. \'[{"name":"kaufpreis","type":"price","aliases":["Preis"]}]\'')
    p_extract.add_argument("--scope")
    p_extract.add_argument("--render", choices=["auto", "on_miss", "never"], default="auto")
    p_extract.add_argument("--no-learn", action="store_true")

    p_recipes = sub.add_parser("recipes", help="list stored recipes")
    p_recipes.add_argument("--scope")

    p_forget = sub.add_parser("forget", help="delete stored recipes")
    p_forget.add_argument("--scope", required=True)
    p_forget.add_argument("--field")

    args = parser.parse_args(argv)
    policy = FetchPolicy.from_env()
    if args.ignore_robots:
        policy.respect_robots = False
    if args.min_interval is not None:
        policy.min_interval_seconds = args.min_interval
    tool = DragonFruitMe(state_dir=args.state_dir, policy=policy)

    if args.command == "observe":
        result = tool.observe(**_source(args))
    elif args.command == "locate":
        result = tool.locate(query=args.query, max_results=args.max_results, **_source(args))
    elif args.command == "extract":
        result = tool.extract(fields=args.fields_json, scope=args.scope, render=args.render,
                              learn=not args.no_learn, **_source(args))
    elif args.command == "recipes":
        result = tool.recipes(scope=args.scope)
    else:
        result = tool.forget(scope=args.scope, field=args.field)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    raise SystemExit(0 if result.get("ok") else 2)


if __name__ == "__main__":
    main()
