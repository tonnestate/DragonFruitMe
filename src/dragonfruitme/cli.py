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


def _read_urls(path: str) -> list[str]:
    """Read an explicit URL set from a JSON array or a line-delimited text file."""
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("URL JSON must be an array")
        values = [str(value).strip() for value in data]
    else:
        values = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        urls.append(value)
    return urls


def _is_final(row: dict) -> bool:
    """A row is final if it succeeded or failed for a reason a retry cannot fix.

    Transient failures (``recoverable: true`` such as FETCH_FAILED or HTTP 5xx)
    are retried on ``--resume``; BLOCKED, ROBOTS_DISALLOWED and other
    non-recoverable errors are not.
    """
    if row.get("ok") is True:
        return True
    error = row.get("error")
    return isinstance(error, dict) and error.get("recoverable") is False


def _resume_urls(path: Path) -> set[str]:
    """Return URLs whose latest durably written JSONL row is final."""
    latest: dict[str, bool] = {}
    if not path.exists():
        return set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn line from an interrupted write: its URL is retried
        url = row.get("url") if isinstance(row, dict) else None
        if isinstance(url, str) and url:
            latest[url] = _is_final(row)
    return {url for url, final in latest.items() if final}


def _ensure_line_boundary(path: Path) -> None:
    """Terminate a torn last line so appended rows stay valid JSONL."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb") as handle:
        handle.seek(-1, 2)
        last = handle.read(1)
    if last != b"\n":
        with path.open("ab") as handle:
            handle.write(b"\n")


def _batch_extract(args: argparse.Namespace, tool: DragonFruitMe) -> int:
    """Process an explicit URL set without turning the agent into a loop/merge engine."""
    urls = _read_urls(args.urls_file)
    output_path = Path(args.output).expanduser() if args.output else None
    done = _resume_urls(output_path) if (args.resume and output_path is not None) else set()

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if args.resume:
            _ensure_line_boundary(output_path)
        mode = "a" if args.resume else "w"
        stream = output_path.open(mode, encoding="utf-8", newline="\n")
        close_stream = True
    else:
        stream = sys.stdout
        close_stream = False

    counts = {
        "total": len(urls),
        "skipped": 0,
        "attempted": 0,
        "complete": 0,
        "partial": 0,
        "incomplete": 0,
        "errors": 0,
        "unconfirmed_fields": 0,
        "flagged_rows": 0,
        "error_codes": {},
        "field_statuses": {},
        "stage_hits": {},
        "signals": {},
    }

    try:
        for url in urls:
            if url in done:
                counts["skipped"] += 1
                continue
            counts["attempted"] += 1
            result = tool.extract(
                fields=args.fields_json,
                url=url,
                render=args.render,
                learn=not args.no_learn,
            )
            row = {"url": url, **result}
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()

            if not result.get("ok"):
                counts["errors"] += 1
                error = result.get("error") or {}
                code = str(error.get("code") or "UNKNOWN_ERROR")
                counts["error_codes"][code] = counts["error_codes"].get(code, 0) + 1
                continue
            status = str(result.get("status", "")).lower()
            if status in {"complete", "partial", "incomplete"}:
                counts[status] += 1
            else:
                counts["errors"] += 1
            fields = result.get("fields") or []
            counts["unconfirmed_fields"] += sum(1 for f in fields if f.get("status") == "UNCONFIRMED")
            if any(f.get("signals") for f in fields):
                counts["flagged_rows"] += 1
            for field in fields:
                field_status = str(field.get("status") or "UNKNOWN")
                counts["field_statuses"][field_status] = counts["field_statuses"].get(field_status, 0) + 1
                stage = field.get("stage")
                if stage:
                    stage = str(stage)
                    counts["stage_hits"][stage] = counts["stage_hits"].get(stage, 0) + 1
                for signal in field.get("signals") or []:
                    code = str(signal.get("code") or "UNKNOWN_SIGNAL")
                    counts["signals"][code] = counts["signals"].get(code, 0) + 1
    finally:
        if close_stream:
            stream.close()

    summary = {"ok": True, "batch": counts}
    sys.stderr.write(json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.fail_on_error and counts["errors"]:
        return 2
    return 0


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

    p_batch = sub.add_parser(
        "extract-batch",
        help="extract the same fields from an explicit URL set; JSONL output is flushed after every URL",
    )
    p_batch.add_argument("--urls-file", required=True, help="newline-delimited URLs or a JSON array of URLs")
    p_batch.add_argument("--fields-json", type=_json, required=True)
    p_batch.add_argument("--output", help="JSONL output path; stdout when omitted")
    p_batch.add_argument("--resume", action="store_true",
                         help="append to --output; skip URLs whose last row is final, retry transient failures")
    p_batch.add_argument("--render", choices=["auto", "on_miss", "never"], default="auto")
    p_batch.add_argument("--no-learn", action="store_true")
    p_batch.add_argument("--fail-on-error", action="store_true",
                         help="exit 2 if any URL failed; default is progress-preserving exit 0")

    p_recipes = sub.add_parser("recipes", help="list stored recipes")
    p_recipes.add_argument("--scope")

    p_forget = sub.add_parser("forget", help="delete stored recipes")
    p_forget.add_argument("--scope", required=True)
    p_forget.add_argument("--field")

    args = parser.parse_args(argv)
    if args.command == "extract-batch" and args.resume and not args.output:
        parser.error("--resume requires --output")

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
    elif args.command == "extract-batch":
        raise SystemExit(_batch_extract(args, tool))
    elif args.command == "recipes":
        result = tool.recipes(scope=args.scope)
    else:
        result = tool.forget(scope=args.scope, field=args.field)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    raise SystemExit(0 if result.get("ok") else 2)


if __name__ == "__main__":
    main()
