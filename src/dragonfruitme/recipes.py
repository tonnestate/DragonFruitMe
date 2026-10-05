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


ROLES = ("primary", "fallback", "candidate", "retired")
LIVE_ROLES = ("primary", "fallback", "candidate")
MAX_LIVE = 4            # primary + up to three alternatives per (scope, field)
RETIRED_KEEP = 16       # bounded audit history of retired recipes per (scope, field)
PROMOTE_MIN_HITS = 2    # an alternative must have proven itself at least twice
KIND_PRIOR = {"json": 3, "regex": 2, "label": 1}  # tie-break only, never overrides evidence
SCHEMA_VERSION = 3
_LIVE_SQL = "role IN ('primary','fallback','candidate')"  # an IN list keeps the bucket index usable
_COLUMNS = ("id, scope, field, kind, pattern, origin, role, hits, misses, total_misses, "
            "contradictions, confirmed_hits, last_validated")


@dataclass
class Recipe:
    """One deterministic way to find a field on pages of one scope.

    ``misses`` counts consecutive misses (reset by a hit); ``total_misses``,
    ``contradictions`` and ``confirmed_hits`` are lifetime evidence used to
    rank alternatives.
    """

    scope: str
    field: str
    kind: str
    pattern: str
    origin: str
    hits: int = 0
    misses: int = 0
    role: str = "primary"
    id: int | None = None
    total_misses: int = 0
    contradictions: int = 0
    confirmed_hits: int = 0
    last_validated: float | None = None

    @property
    def status(self) -> str:
        if self.role == "retired":
            return "retired"
        return "stale" if self.misses >= STALE_AFTER_MISSES else "active"

    def score(self) -> float:
        """Laplace-smoothed reliability; contradictions weigh double."""
        return (self.hits + 1) / (self.hits + self.total_misses + 2 * self.contradictions + 2)

    def sort_key(self) -> tuple[float, int, int]:
        return (-self.score(), -KIND_PRIOR.get(self.kind, 0), self.id or 0)

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "Recipe":
        (rid, scope, field, kind, pattern, origin, role, hits, misses, total_misses,
         contradictions, confirmed_hits, last_validated) = row
        return cls(scope=scope, field=field, kind=kind, pattern=pattern, origin=origin, hits=hits, misses=misses,
                   role=role, id=rid, total_misses=total_misses, contradictions=contradictions,
                   confirmed_hits=confirmed_hits, last_validated=last_validated)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "field": self.field,
            "role": self.role,
            "kind": self.kind,
            "pattern": self.pattern,
            "origin": self.origin,
            "hits": self.hits,
            "misses": self.misses,
            "total_misses": self.total_misses,
            "contradictions": self.contradictions,
            "confirmed_hits": self.confirmed_hits,
            "last_validated": self.last_validated,
            "score": round(self.score(), 4),
            "status": self.status,
        }


class RecipeStore:
    """SQLite-backed recipe portfolio and observation store.

    Each ``(scope, field)`` keeps a small portfolio: one ``primary`` recipe,
    proven ``fallback`` recipes and new ``candidate`` recipes (at most
    ``MAX_LIVE`` live ones; the weakest alternative is ``retired`` beyond that).
    A newly compiled recipe never overwrites a working primary - it joins as a
    candidate and has to prove itself when the primary actually fails.

    One connection per store (WAL journal, ``synchronous=NORMAL``). Writes are
    grouped with :meth:`transaction`; the extractor commits once per page and
    stage group. A crash can lose at most the counters of the page being
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
        with self.transaction() as db:
            self._migrate(db)

    # -- schema --------------------------------------------------------------
    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS recipe_set (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                field TEXT NOT NULL,
                kind TEXT NOT NULL,
                pattern TEXT NOT NULL,
                origin TEXT NOT NULL,
                role TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0,
                misses INTEGER NOT NULL DEFAULT 0,
                total_misses INTEGER NOT NULL DEFAULT 0,
                contradictions INTEGER NOT NULL DEFAULT 0,
                confirmed_hits INTEGER NOT NULL DEFAULT 0,
                last_validated REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (scope, field, kind, pattern)
            )
            """
        )
        # Dispatch index: one bucket per (scope, field, role). Lookups touch only the live
        # recipes of one bucket, independent of how many recipes the store holds overall
        # and of how many were retired in that bucket.
        db.execute("DROP INDEX IF EXISTS recipe_set_scope_field")
        db.execute("CREATE INDEX IF NOT EXISTS recipe_set_bucket ON recipe_set (scope, field, role)")
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
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recipes'").fetchone()
        if legacy:
            # v0.1-v0.3: one recipe per (scope, field) -> becomes that field's primary
            db.execute(
                """
                INSERT OR IGNORE INTO recipe_set
                    (scope, field, kind, pattern, origin, role, hits, misses, total_misses,
                     contradictions, confirmed_hits, last_validated, created_at, updated_at)
                SELECT scope, field, kind, pattern, origin, 'primary', hits, misses, misses,
                       0, 0, NULL, created_at, updated_at
                FROM recipes
                """
            )
            db.execute("DROP TABLE recipes")
        # v2 -> v3: bounded retired history (the bucket index is created above)
        buckets = db.execute(
            "SELECT scope, field FROM recipe_set WHERE role='retired' GROUP BY scope, field HAVING COUNT(*) > ?",
            (RETIRED_KEEP,),
        ).fetchall()
        for scope, field in buckets:
            RecipeStore._trim_retired(db, scope, field)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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

    # -- reads ---------------------------------------------------------------
    def recipe_set(self, scope: str, field: str) -> list[Recipe]:
        """Live recipes in execution order: primary first, then alternatives by evidence."""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM recipe_set WHERE scope=? AND field=? AND {_LIVE_SQL}",
                (scope, field),
            ).fetchall()
        recipes = [Recipe.from_row(row) for row in rows]
        recipes.sort(key=lambda r: (r.role != "primary", *r.sort_key()))
        return recipes

    def get(self, scope: str, field: str) -> Recipe | None:
        """The primary recipe of a field, if any."""
        for recipe in self.recipe_set(scope, field):
            if recipe.role == "primary":
                return recipe
        return None

    def list(self, scope: str | None = None, include_retired: bool = True) -> list[Recipe]:
        query = f"SELECT {_COLUMNS} FROM recipe_set"
        clauses: list[str] = []
        args: list[Any] = []
        if scope:
            clauses.append("scope=?")
            args.append(scope)
        if not include_retired:
            clauses.append(_LIVE_SQL)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._connect() as db:
            recipes = [Recipe.from_row(row) for row in db.execute(query, args).fetchall()]
        order = {role: i for i, role in enumerate(ROLES)}
        recipes.sort(key=lambda r: (r.scope, r.field, order.get(r.role, 9), *r.sort_key()))
        return recipes

    # -- writes --------------------------------------------------------------
    def _find(self, db: sqlite3.Connection, recipe: Recipe) -> Recipe | None:
        row = db.execute(
            f"SELECT {_COLUMNS} FROM recipe_set WHERE scope=? AND field=? AND kind=? AND pattern=?",
            (recipe.scope, recipe.field, recipe.kind, recipe.pattern),
        ).fetchone()
        return Recipe.from_row(row) if row else None

    def _has_primary(self, db: sqlite3.Connection, scope: str, field: str) -> bool:
        return db.execute(
            "SELECT 1 FROM recipe_set WHERE scope=? AND field=? AND role='primary' LIMIT 1", (scope, field)
        ).fetchone() is not None

    def _insert(self, db: sqlite3.Connection, recipe: Recipe, role: str, hits: int) -> int:
        now = time.time()
        cur = db.execute(
            """
            INSERT INTO recipe_set (scope, field, kind, pattern, origin, role, hits, misses, total_misses,
                                    contradictions, confirmed_hits, last_validated, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?)
            """,
            (recipe.scope, recipe.field, recipe.kind, recipe.pattern, recipe.origin, role, hits,
             now if hits else None, now, now),
        )
        return int(cur.lastrowid)

    def _enforce_cap(self, db: sqlite3.Connection, scope: str, field: str) -> None:
        rows = db.execute(
            f"SELECT {_COLUMNS} FROM recipe_set WHERE scope=? AND field=? AND role IN ('fallback','candidate')",
            (scope, field),
        ).fetchall()
        alternatives = sorted((Recipe.from_row(r) for r in rows), key=lambda r: r.sort_key())
        excess = len(alternatives) - (MAX_LIVE - 1)
        if excess <= 0:
            return
        for weakest in alternatives[::-1][:excess]:
            db.execute("UPDATE recipe_set SET role='retired', updated_at=? WHERE id=?", (time.time(), weakest.id))
        self._trim_retired(db, scope, field)

    @staticmethod
    def _trim_retired(db: sqlite3.Connection, scope: str, field: str) -> None:
        """Keep only the newest ``RETIRED_KEEP`` retired recipes of a bucket."""
        db.execute(
            """
            DELETE FROM recipe_set WHERE id IN (
                SELECT id FROM recipe_set WHERE scope=? AND field=? AND role='retired'
                ORDER BY updated_at DESC, id DESC LIMIT -1 OFFSET ?
            )
            """,
            (scope, field, RETIRED_KEEP),
        )

    def add(self, recipe: Recipe) -> tuple[Recipe, bool]:
        """Add a freshly compiled, already validated recipe to the portfolio.

        Returns ``(recipe, created)``. The first recipe of a field becomes its
        primary; later ones join as candidates and never displace a working
        primary. The page that produced the recipe counts as its first hit.
        """
        with self._connect() as db:
            existing = self._find(db, recipe)
            if existing is not None:
                if existing.role == "retired":
                    db.execute("UPDATE recipe_set SET role='candidate', misses=0, updated_at=? WHERE id=?",
                               (time.time(), existing.id))
                    self._enforce_cap(db, recipe.scope, recipe.field)
                    existing = self._find(db, recipe)
                return existing, False
            role = "candidate" if self._has_primary(db, recipe.scope, recipe.field) else "primary"
            self._insert(db, recipe, role, hits=1)
            self._enforce_cap(db, recipe.scope, recipe.field)
            return self._find(db, recipe), True

    def put(self, recipe: Recipe) -> None:
        """Install a recipe as the primary (host/maintenance use).

        The previous primary is kept as a fallback, not deleted.
        """
        with self._connect() as db:
            now = time.time()
            db.execute("UPDATE recipe_set SET role='fallback', updated_at=? WHERE scope=? AND field=? AND role='primary'",
                       (now, recipe.scope, recipe.field))
            existing = self._find(db, recipe)
            if existing is None:
                self._insert(db, recipe, "primary", hits=0)
            else:
                db.execute("UPDATE recipe_set SET role='primary', origin=?, misses=0, updated_at=? WHERE id=?",
                           (recipe.origin, now, existing.id))
            self._enforce_cap(db, recipe.scope, recipe.field)

    def record_hit(self, recipe_id: int, *, confirmed: bool = False) -> None:
        with self._connect() as db:
            now = time.time()
            db.execute(
                "UPDATE recipe_set SET hits=hits+1, misses=0, confirmed_hits=confirmed_hits+?, "
                "last_validated=?, updated_at=? WHERE id=?",
                (1 if confirmed else 0, now, now, recipe_id),
            )

    def record_miss(self, recipe_id: int, *, contradicted: bool = False) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE recipe_set SET misses=misses+1, total_misses=total_misses+1, "
                "contradictions=contradictions+?, updated_at=? WHERE id=?",
                (1 if contradicted else 0, time.time(), recipe_id),
            )

    def promote(self, scope: str, field: str, recipe_id: int) -> None:
        """Make ``recipe_id`` the primary; the old primary becomes a fallback."""
        with self._connect() as db:
            now = time.time()
            db.execute("UPDATE recipe_set SET role='fallback', updated_at=? WHERE scope=? AND field=? AND role='primary'",
                       (now, scope, field))
            db.execute("UPDATE recipe_set SET role='primary', updated_at=? WHERE id=?", (now, recipe_id))
            # a candidate that answered when needed has proven itself: other candidates stay candidates
            self._enforce_cap(db, scope, field)

    def mark_fallback(self, recipe_id: int) -> None:
        """A candidate that answered when the primary failed is a proven fallback."""
        with self._connect() as db:
            db.execute("UPDATE recipe_set SET role='fallback', updated_at=? WHERE id=? AND role='candidate'",
                       (time.time(), recipe_id))

    def record(self, scope: str, field: str, ok: bool) -> str:
        """Compatibility helper: count a hit or miss on the primary and return its status."""
        primary = self.get(scope, field)
        if primary is None or primary.id is None:
            return "missing"
        with self._connect():
            if ok:
                self.record_hit(primary.id)
            else:
                self.record_miss(primary.id)
        updated = self.get(scope, field)
        return updated.status if updated else "missing"

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

    def forget(self, scope: str, field: str | None = None) -> int:
        with self._connect() as db:
            if field is None:
                cur = db.execute("DELETE FROM recipe_set WHERE scope=?", (scope,))
                db.execute("DELETE FROM observations WHERE scope=?", (scope,))
            else:
                cur = db.execute("DELETE FROM recipe_set WHERE scope=? AND field=?", (scope, field))
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
