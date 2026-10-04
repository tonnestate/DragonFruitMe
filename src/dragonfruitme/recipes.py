"""Compile-once extraction recipes.

When an expensive stage (structured search, label heuristics, rendering or an
agent-taught value) finds a field, DragonFruitMe compiles the finding into a
cheap deterministic recipe and stores it per ``(scope, field)``:

* ``json``  - a ``source|dotted.path`` into structured data (most stable)
* ``regex`` - a left-anchored regex with a typed capture group (raw HTML)
* ``label`` - the visible label text whose value follows it

On the next page of the same template, stage 1 runs the recipe in
microseconds. A recipe that stops validating is marked stale and the graph
escalates again.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .values import CAPTURE_PATTERNS, HTML_WS, equivalent, normalize_text, value_regex

ANCHOR_LENGTHS = (16, 24, 32, 48, 64, 96, 128, 160)
MAX_OCCURRENCES = 8
LABEL_WINDOW = 300
STALE_AFTER_MISSES = 2
_DIGIT_RUN = re.compile(r"\d{3,}")


@dataclass
class Recipe:
    scope: str
    field: str
    kind: str
    pattern: str
    origin: str
    hits: int = 0
    misses: int = 0
    status: str = "active"

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "field": self.field,
            "kind": self.kind,
            "pattern": self.pattern,
            "origin": self.origin,
            "hits": self.hits,
            "misses": self.misses,
            "status": self.status,
        }


class RecipeStore:
    """SQLite-backed recipe and observation store.

    One connection per store (WAL journal, ``synchronous=NORMAL``) instead of a
    connection per call. Writes are grouped with :meth:`transaction`; the
    extractor commits once per page and stage group rather than once per field
    and operation. A crash can lose at most the counters of the page being
    processed, never a previously committed recipe.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        # isolation_level=None: no implicit transactions; transaction() issues BEGIN/COMMIT itself
        self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        self._finalizer = weakref.finalize(self, self._db.close)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS recipes (
                    scope TEXT NOT NULL,
                    field TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0,
                    misses INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, field)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    scope TEXT NOT NULL,
                    field TEXT NOT NULL,
                    last_url TEXT NOT NULL,
                    last_value TEXT NOT NULL,
                    streak INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, field)
                )
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Group reads and writes into one transaction; nested calls join the outer one.

        The outer level takes the write lock up front (``BEGIN IMMEDIATE``) so a
        concurrent writer in another process waits on the busy timeout instead
        of failing on a stale read snapshot.
        """
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._db.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._db
            except BaseException:
                self._depth -= 1
                if outer:
                    self._db.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._db.execute("COMMIT")

    _connect = transaction

    def close(self) -> None:
        self._finalizer()

    def get(self, scope: str, field: str) -> Recipe | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT scope, field, kind, pattern, origin, hits, misses, status FROM recipes WHERE scope=? AND field=?",
                (scope, field),
            ).fetchone()
        return Recipe(*row) if row else None

    def put(self, recipe: Recipe) -> None:
        now = time.time()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO recipes (scope, field, kind, pattern, origin, hits, misses, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, 0, 'active', ?, ?)
                ON CONFLICT(scope, field) DO UPDATE SET
                    kind=excluded.kind, pattern=excluded.pattern, origin=excluded.origin,
                    hits=0, misses=0, status='active', updated_at=excluded.updated_at
                """,
                (recipe.scope, recipe.field, recipe.kind, recipe.pattern, recipe.origin, now, now),
            )

    def record(self, scope: str, field: str, ok: bool) -> str:
        """Count a hit or miss and return the resulting status."""
        with self._connect() as db:
            if ok:
                db.execute(
                    "UPDATE recipes SET hits=hits+1, misses=0, status='active', updated_at=? WHERE scope=? AND field=?",
                    (time.time(), scope, field),
                )
            else:
                db.execute(
                    "UPDATE recipes SET misses=misses+1, status=CASE WHEN misses+1>=? THEN 'stale' ELSE status END, updated_at=? "
                    "WHERE scope=? AND field=?",
                    (STALE_AFTER_MISSES, time.time(), scope, field),
                )
            row = db.execute("SELECT status FROM recipes WHERE scope=? AND field=?", (scope, field)).fetchone()
        return row[0] if row else "missing"

    def observe(self, scope: str, field: str, url: str, value: str) -> int:
        """Record a found value and return how many *different* consecutive URLs
        of this scope produced exactly this value (1 = first time).

        Re-extracting the same URL does not count as repetition.
        """
        with self._connect() as db:
            row = db.execute(
                "SELECT last_url, last_value, streak FROM observations WHERE scope=? AND field=?", (scope, field)
            ).fetchone()
            if row is None:
                streak = 1
            elif row[0] == url:
                streak = row[2] if row[1] == value else 1
            elif row[1] == value:
                streak = row[2] + 1
            else:
                streak = 1
            db.execute(
                """
                INSERT INTO observations (scope, field, last_url, last_value, streak, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, field) DO UPDATE SET
                    last_url=excluded.last_url, last_value=excluded.last_value,
                    streak=excluded.streak, updated_at=excluded.updated_at
                """,
                (scope, field, url, value, streak, time.time()),
            )
        return streak

    def list(self, scope: str | None = None) -> list[Recipe]:
        query = "SELECT scope, field, kind, pattern, origin, hits, misses, status FROM recipes"
        args: tuple[Any, ...] = ()
        if scope:
            query += " WHERE scope=?"
            args = (scope,)
        query += " ORDER BY scope, field"
        with self._connect() as db:
            return [Recipe(*row) for row in db.execute(query, args).fetchall()]

    def forget(self, scope: str, field: str | None = None) -> int:
        with self._connect() as db:
            if field is None:
                cur = db.execute("DELETE FROM recipes WHERE scope=?", (scope,))
                db.execute("DELETE FROM observations WHERE scope=?", (scope,))
            else:
                cur = db.execute("DELETE FROM recipes WHERE scope=? AND field=?", (scope, field))
                db.execute("DELETE FROM observations WHERE scope=? AND field=?", (scope, field))
            return cur.rowcount


# -- regex recipes --------------------------------------------------------

def apply_regex(pattern: str, html: str) -> str | None:
    try:
        match = re.search(pattern, html, re.S | re.I)
    except re.error:
        return None
    if not match:
        return None
    group = match.group(1) if match.groups() else match.group(0)
    return normalize_text(re.sub(r"<[^>]+>", " ", group))


def _anchor_regex(left: str) -> str:
    """Escape a left context and generalise what changes from page to page.

    Whitespace becomes ``\\s*``. Digit runs inside markup (``data-id="1"``,
    ``id="item-4711"``) become ``\\d+`` because ids differ per page; digits
    in visible text, such as a label "3-Zimmer", stay literal. Long digit
    runs (>= 3) are generalised everywhere.
    """
    # Characters before the first '<' belong to a tag if a '>' closes it first.
    first_open, first_close = left.find("<"), left.find(">")
    in_tag = first_close != -1 and (first_open == -1 or first_close < first_open)
    out: list[str] = []
    i = 0
    while i < len(left):
        ch = left[i]
        if ch.isspace():
            j = i
            while j < len(left) and left[j].isspace():
                j += 1
            out.append(r"\s*")
            i = j
            continue
        if ch.isdigit():
            j = i
            while j < len(left) and left[j].isdigit():
                j += 1
            run = left[i:j]
            out.append(r"\d+" if in_tag or len(run) >= 3 else re.escape(run))
            i = j
            continue
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        out.append(re.escape(ch))
        i += 1
    return "".join(out)


def _left_window(html: str, start: int, length: int) -> str:
    window = html[max(0, start - length):start]
    # Prefer anchors that begin at a tag or a word boundary for stability.
    tag = window.find("<")
    if 0 < tag < len(window) // 2:
        window = window[tag:]
    return window


def _check(pattern: str, html: str, value: str, field_type: str) -> bool:
    """A recipe is valid if its first match equals ``value`` and all matches agree."""
    try:
        compiled = re.compile(pattern, re.S | re.I)
    except re.error:
        return False
    found = apply_regex(pattern, html)
    if found is None or not equivalent(found, value, field_type):
        return False
    values = {normalize_text(m.group(1)) for m in compiled.finditer(html)}
    return len(values) == 1


def derive_regex(html: str, value: str, field_type: str, label: str | None = None) -> str | None:
    """Derive a left-anchored regex whose first match equals ``value``.

    Anchors that contain the visible label ("Kaufpreis") are preferred over
    bare markup anchors (``</th><td>``) because they survive layout changes
    and reordering. Otherwise the shortest unique markup anchor wins. The
    capture group is typed (price, area, ...) or a text node, so the recipe
    keeps working when the value changes on the next page.
    """
    finder = re.compile(value_regex(value), re.I)
    occurrences = [m for m in finder.finditer(html)][:MAX_OCCURRENCES]
    if not occurrences:
        return None
    if field_type in CAPTURE_PATTERNS:
        capture = f"({CAPTURE_PATTERNS[field_type]})"
    else:
        capture = r"([^<]{1,300}?)"

    def build(left: str, occ: re.Match[str]) -> str:
        right_text = html[occ.end():occ.end() + 1]
        if right_text == "<" or not right_text:
            right = r"(?=" + HTML_WS + r"*<)"
        elif field_type not in CAPTURE_PATTERNS:
            # untyped text inside a longer text node ends at its literal delimiter
            right = "(?=" + re.escape(right_text) + ")"
        else:
            right = ""  # typed captures delimit themselves
        return _anchor_regex(left) + HTML_WS + "*" + capture + right

    if label:
        label_re = re.compile(value_regex(label), re.I)
        for occ in occurrences:
            window_start = max(0, occ.start() - LABEL_WINDOW)
            labels = list(label_re.finditer(html, window_start, occ.start()))
            if not labels:
                continue
            left = html[labels[-1].start():occ.start()]
            pattern = build(left, occ)
            if _check(pattern, html, value, field_type):
                return pattern

    for length in ANCHOR_LENGTHS:
        for occ in occurrences:
            left = _left_window(html, occ.start(), length)
            if not left.strip():
                continue
            pattern = build(left, occ)
            if _check(pattern, html, value, field_type):
                return pattern
    return None


def scope_of(url: str) -> str:
    """Recipe scope = host + URL template.

    ``https://www.example.de/expose/123456`` and ``.../expose/987`` share the
    scope ``example.de/expose/*``; a listing page gets its own scope, so
    recipes for different page templates never overwrite each other.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return "inline"
    segments = []
    for segment in [s for s in parts.path.split("/") if s][:4]:
        stem = segment.rsplit(".", 1)[0]
        if any(ch.isdigit() for ch in stem) or (len(stem) > 24 and "-" in stem):
            segments.append("*")
        else:
            segments.append(stem.lower())
    return host + "/" + "/".join(segments)
