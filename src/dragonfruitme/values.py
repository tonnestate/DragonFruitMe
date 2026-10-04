"""Text normalisation, tokenisation, typed value detection and validation.

Everything here is deterministic and dependency-free. Field types are the
contract between an agent ("I need a price") and the escalation graph
("this candidate is or is not a price").
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

_WS = re.compile(r"\s+")
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", " ": " "})
_TOKEN = re.compile(r"[a-z0-9]+")

# Flexible whitespace as it appears in raw HTML.
HTML_WS = r"(?:\s|&nbsp;|&#160;|&#xa0;)"

_CURRENCY = r"(?:€|EUR|&euro;|\$|USD|CHF|£|GBP)"
_NUM = r"\d{1,3}(?:[.,'  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"

# Patterns used to find a typed value inside a text node (normalised text).
TYPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "price": re.compile(rf"(?:{_CURRENCY}\s?(?:{_NUM})|(?:{_NUM})\s?{_CURRENCY})", re.I),
    "area": re.compile(rf"(?:{_NUM})\s?(?:m²|m2|qm|sqm|㎡)", re.I),
    "number": re.compile(rf"-?(?:{_NUM})"),
    "integer": re.compile(r"-?\d+"),
    "date": re.compile(r"\b(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})\b"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"\+?\d[\d ()/\-]{5,}\d"),
    "url": re.compile(r"https?://[^\s\"'<>]+"),
}

# Patterns used inside derived regex recipes (raw HTML side).
CAPTURE_PATTERNS: dict[str, str] = {
    "price": rf"(?:{_CURRENCY}{HTML_WS}*(?:{_NUM})|(?:{_NUM}){HTML_WS}*{_CURRENCY})",
    "area": rf"(?:{_NUM}){HTML_WS}*(?:m²|m2|qm|sqm|m&sup2;|㎡)",
    "number": rf"-?(?:{_NUM})",
    "integer": r"-?\d+",
    "date": r"(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "phone": r"\+?\d[\d ()/\-]{5,}\d",
    "url": r"https?://[^\s\"'<>]+",
}

FIELD_TYPES = frozenset({"text", *TYPE_PATTERNS})
NUMERIC_TYPES = frozenset({"price", "area", "number", "integer"})


def normalize_text(value: Any) -> str:
    """Unescape HTML entities and collapse whitespace."""
    if value is None:
        return ""
    text = _html.unescape(str(value)).replace(" ", " ")
    return _WS.sub(" ", text).strip()


def fold(value: str) -> str:
    """Lower-case and fold German umlauts for matching (never for output)."""
    return normalize_text(value).lower().translate(_FOLD)


def tokenize(value: str) -> list[str]:
    return _TOKEN.findall(fold(value))


def parse_number(raw: str) -> float | None:
    """Parse German and English number notations.

    ``349.000`` -> 349000, ``349.000,50`` -> 349000.5, ``1,200.50`` -> 1200.5,
    ``72,5`` -> 72.5, ``72.5`` -> 72.5.
    """
    text = normalize_text(raw)
    match = re.search(r"-?\d[\d.,' ]*", text)
    if not match:
        return None
    num = match.group(0).strip().replace("'", "").replace(" ", "")
    num = num.rstrip(".,")
    negative = num.startswith("-")
    num = num.lstrip("-")
    if not num:
        return None
    if "." in num and "," in num:
        decimal = "." if num.rfind(".") > num.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        num = num.replace(thousands, "").replace(decimal, ".")
    elif "," in num:
        # Several commas can only be thousands separators ("1,200,000").
        # A single comma is read as the German decimal separator ("72,5").
        num = num.replace(",", "") if num.count(",") > 1 else num.replace(",", ".")
    elif "." in num:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", num):
            num = num.replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None
    return -value if negative else value


def find_typed(text: str, field_type: str) -> str | None:
    """Return the first substring of ``text`` that looks like ``field_type``."""
    text = normalize_text(text)
    if not text:
        return None
    if field_type == "text":
        return text
    pattern = TYPE_PATTERNS.get(field_type)
    if pattern is None:
        return None
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def validate(raw: Any, spec: dict[str, Any], *, typed_source: bool = False) -> tuple[bool, Any, str]:
    """Validate a candidate against a field spec.

    Returns ``(ok, normalized_value, reason)``. ``normalized_value`` is a float
    for numeric types and the normalised text otherwise. ``typed_source`` marks
    values from structured data, where the key already carries the meaning: a
    bare ``349000`` under ``offers.price`` is a price without a currency sign.
    """
    field_type = spec.get("type", "text")
    text = normalize_text(raw)
    if not text:
        return False, None, "EMPTY"
    max_chars = int(spec.get("max_chars", 500))
    if len(text) > max_chars:
        return False, None, "TOO_LONG"
    custom = spec.get("pattern")
    if custom:
        try:
            if not re.search(custom, text):
                return False, None, "PATTERN_MISMATCH"
        except re.error:
            return False, None, "INVALID_PATTERN"
    if field_type == "text":
        return True, text, "OK"
    pattern = TYPE_PATTERNS.get(field_type)
    if pattern is None:
        return False, None, "UNKNOWN_TYPE"
    match = pattern.search(text)
    if not match and typed_source and field_type in {"price", "area"}:
        match = TYPE_PATTERNS["number"].fullmatch(text)
    if not match:
        return False, None, "TYPE_MISMATCH"
    if field_type in NUMERIC_TYPES:
        number = parse_number(match.group(0))
        if number is None:
            return False, None, "TYPE_MISMATCH"
        if field_type == "integer" and number != int(number):
            return False, None, "TYPE_MISMATCH"
        if "min" in spec and number < float(spec["min"]):
            return False, None, "BELOW_MIN"
        if "max" in spec and number > float(spec["max"]):
            return False, None, "ABOVE_MAX"
        return True, int(number) if field_type == "integer" else number, "OK"
    return True, match.group(0).strip(), "OK"


def equivalent(a: Any, b: Any, field_type: str) -> bool:
    """Compare two candidate values the way the field type would."""
    if field_type in NUMERIC_TYPES:
        na, nb = parse_number(str(a)), parse_number(str(b))
        return na is not None and nb is not None and abs(na - nb) < 1e-9
    return fold(str(a)) == fold(str(b))


def value_regex(value: str) -> str:
    """Regex that finds a literal value in raw HTML with flexible whitespace/entities."""
    text = normalize_text(value)
    parts: list[str] = []
    for chunk in re.split(r"(\s+)", text):
        if not chunk:
            continue
        if chunk.isspace():
            parts.append(HTML_WS + "+")
            continue
        escaped = []
        for ch in chunk:
            if ch == "€":
                escaped.append("(?:€|&euro;|&#8364;)")
            elif ch == "²":
                escaped.append("(?:²|&sup2;|&#178;)")
            elif ch == "&":
                escaped.append("(?:&|&amp;)")
            else:
                escaped.append(re.escape(ch))
        parts.append("".join(escaped))
    return "".join(parts)
