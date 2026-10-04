"""Fetch policy: what DragonFruitMe is allowed to do on the network.

The policy is fixed by the host (constructor, CLI flags or MCP environment),
never by the agent per call. DragonFruitMe identifies itself honestly, honours
robots.txt by default, rate-limits per host and treats CAPTCHAs and bot
challenges as a hard stop. There is no stealth, fingerprint spoofing, proxy
rotation or CAPTCHA solving - by design.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping

from . import __version__

DEFAULT_USER_AGENT = f"DragonFruitMe/{__version__} (+https://github.com/tonnestate/DragonFruitMe)"

# Signals that a site is asking for a human. A detected challenge ends the
# escalation with BLOCKED; DragonFruitMe never tries to get past it.
TECHNICAL_MARKERS = (
    "g-recaptcha", "grecaptcha", "h-captcha", "hcaptcha.com", "cf-chl-", "challenge-platform",
    "cf_chl_opt", "cf-turnstile", "captcha-delivery", "datadome", "px-captcha", "_incapsula_",
)
PHRASE_MARKERS = (
    "are you a robot", "are you human", "verify you are human", "unusual traffic",
    "access denied", "bitte bestätigen sie, dass sie kein roboter sind",
)
_TECHNICAL_RE = re.compile("|".join(re.escape(m) for m in TECHNICAL_MARKERS), re.I)
_PHRASE_RE = re.compile("|".join(re.escape(m) for m in PHRASE_MARKERS), re.I)
_TAGS_RE = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", re.I | re.S)
CHALLENGE_STATUSES = {202, 401, 403, 503}
THIN_PAGE_CHARS = 600


@dataclass
class FetchPolicy:
    user_agent: str = DEFAULT_USER_AGENT
    respect_robots: bool = True
    timeout_seconds: float = 20.0
    max_bytes: int = 5_000_000
    min_interval_seconds: float = 1.0
    allowed_schemes: tuple[str, ...] = ("http", "https")
    deny_hosts: tuple[str, ...] = field(default_factory=tuple)
    cache_ttl_seconds: float = 300.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "FetchPolicy":
        env = os.environ if environ is None else environ
        policy = cls()
        if env.get("DRAGONFRUITME_USER_AGENT"):
            policy.user_agent = env["DRAGONFRUITME_USER_AGENT"]
        if env.get("DRAGONFRUITME_RESPECT_ROBOTS", "").strip().lower() in {"0", "false", "no"}:
            policy.respect_robots = False
        if env.get("DRAGONFRUITME_TIMEOUT"):
            policy.timeout_seconds = float(env["DRAGONFRUITME_TIMEOUT"])
        if env.get("DRAGONFRUITME_MIN_INTERVAL"):
            policy.min_interval_seconds = float(env["DRAGONFRUITME_MIN_INTERVAL"])
        if env.get("DRAGONFRUITME_DENY_HOSTS"):
            policy.deny_hosts = tuple(h.strip().lower() for h in env["DRAGONFRUITME_DENY_HOSTS"].split(",") if h.strip())
        return policy


def visible_chars(html: str) -> int:
    return len(" ".join(_TAGS_RE.sub(" ", html[:500_000]).split()))


def detect_challenge(status: int, html: str) -> str | None:
    """Return a reason string if the response is a bot challenge or block.

    A CAPTCHA widget inside an otherwise normal page (e.g. a contact form) is
    not a block: the content is readable. A challenge is a page that *is* the
    CAPTCHA - a challenge status code or a thin page carrying the marker.
    """
    if status == 429:
        return "RATE_LIMITED"
    head = html[:200_000]
    challenge_status = status in CHALLENGE_STATUSES
    thin = visible_chars(head) < THIN_PAGE_CHARS
    technical = _TECHNICAL_RE.search(head)
    if technical and (challenge_status or thin):
        return f"CHALLENGE:{technical.group(0).lower()}"
    phrase = _PHRASE_RE.search(head)
    if phrase and challenge_status and thin:
        return f"CHALLENGE:{phrase.group(0).lower()}"
    if status in {401, 403}:
        return f"HTTP_{status}"
    return None
