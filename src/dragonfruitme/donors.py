"""Optional host-configured web donors.

Donors are deliberately downstream of DragonFruitMe's own fetch policy. They
receive only HTML that DragonFruitMe has already acquired, so enabling a donor
does not grant it authority to bypass robots.txt, rate limits, host deny-lists,
CAPTCHAs or bot challenges.

The public agent surface stays observe -> locate -> extract. Donor selection is
host-owned through environment configuration or explicit Python injection.
"""
from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .errors import DragonFruitMeError


class WebDonor(Protocol):
    name: str

    def extract(self, *, url: str, fields: list[dict[str, Any]], html: str) -> dict[str, Any]:
        """Return {"donor": str, "values": {field: value}, "evidence": {...}}."""
        ...

    def status(self) -> dict[str, Any]:
        ...


def _config_json(name: str) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DragonFruitMeError(
            "DONOR_CONFIG_INVALID",
            f"{name} must contain a JSON object.",
            recoverable=False,
        ) from exc
    if not isinstance(value, dict):
        raise DragonFruitMeError(
            "DONOR_CONFIG_INVALID",
            f"{name} must contain a JSON object.",
            recoverable=False,
        )
    return value


def _field_prompt(fields: list[dict[str, Any]]) -> str:
    compact = [
        {
            "name": field["name"],
            "type": field.get("type", "text"),
            "aliases": field.get("aliases") or [field["name"]],
        }
        for field in fields
    ]
    return (
        "Extract only the requested fields from the supplied page content. "
        "Do not infer, guess, summarize or use outside knowledge. Return null "
        "when a field is not explicitly supported by the page. Preserve exact "
        "visible values, including units, where practical. Return one JSON "
        "object whose keys are exactly the requested field names. Requested fields: "
        + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    )


def _json_schema(fields: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in fields:
        field_type = field.get("type", "text")
        if field_type == "integer":
            types = ["integer", "string", "null"]
        elif field_type in {"number", "price", "area"}:
            types = ["number", "string", "null"]
        else:
            types = ["string", "null"]
        properties[field["name"]] = {"type": types}
    return {
        "type": "object",
        "properties": properties,
        "required": [field["name"] for field in fields],
        "additionalProperties": False,
    }


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    for attr in ("json_data", "json", "data", "result", "results"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, dict):
            return candidate
    return {}


def _values_for_fields(payload: Any, fields: list[dict[str, Any]]) -> dict[str, Any]:
    mapping = _mapping(payload)
    for key in ("json", "json_data", "result", "results", "data"):
        nested = mapping.get(key)
        if isinstance(nested, dict) and any(field["name"] in nested for field in fields):
            mapping = nested
            break
    return {
        field["name"]: mapping.get(field["name"])
        for field in fields
        if field["name"] in mapping
    }


@dataclass
class ScrapeGraphOssDonor:
    """Self-hosted ScrapeGraphAI SmartScraperGraph donor."""

    config: dict[str, Any] | None = None
    graph_factory: Callable[..., Any] | None = None
    name: str = "scrapegraph-oss"

    def _config(self) -> dict[str, Any]:
        config = dict(
            self.config
            if self.config is not None
            else _config_json("DRAGONFRUITME_SCRAPEGRAPH_OSS_CONFIG_JSON")
        )
        if not config:
            raise DragonFruitMeError(
                "DONOR_NOT_READY",
                "ScrapeGraph OSS is selected but DRAGONFRUITME_SCRAPEGRAPH_OSS_CONFIG_JSON is empty.",
                recoverable=False,
            )
        config.setdefault("verbose", False)
        return config

    def _factory(self) -> Callable[..., Any]:
        if self.graph_factory is not None:
            return self.graph_factory
        try:
            from scrapegraphai.graphs import SmartScraperGraph
        except ImportError as exc:
            raise DragonFruitMeError(
                "DONOR_NOT_INSTALLED",
                "Install the optional ScrapeGraph OSS donor with "
                "pip install 'dragonfruitme[scrapegraph-oss]'.",
                recoverable=False,
            ) from exc
        return SmartScraperGraph

    def extract(self, *, url: str, fields: list[dict[str, Any]], html: str) -> dict[str, Any]:
        if not html.strip():
            raise DragonFruitMeError("DONOR_INPUT_EMPTY", "ScrapeGraph donor received empty HTML.")
        graph = self._factory()(
            prompt=_field_prompt(fields),
            source=html,
            config=self._config(),
        )
        try:
            payload = graph.run()
        except Exception as exc:
            raise DragonFruitMeError(
                "DONOR_FAILED",
                f"ScrapeGraph OSS extraction failed ({type(exc).__name__}).",
            ) from exc
        return {
            "donor": self.name,
            "values": _values_for_fields(payload, fields),
            "evidence": {"source": "dragonfruit_html", "url": url},
        }

    def status(self) -> dict[str, Any]:
        installed = self.graph_factory is not None or importlib.util.find_spec("scrapegraphai") is not None
        configured = bool(
            self.config
            if self.config is not None
            else os.environ.get("DRAGONFRUITME_SCRAPEGRAPH_OSS_CONFIG_JSON", "").strip()
        )
        return {
            "name": self.name,
            "installed": installed,
            "configured": configured,
            "data_egress": "depends_on_host_llm_config",
        }


@dataclass
class ScrapeGraphApiDonor:
    """Managed ScrapeGraph API donor over HTML already acquired by DragonFruitMe."""

    client_factory: Callable[[], Any] | None = None
    name: str = "scrapegraph-api"

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        try:
            from scrapegraph_py import ScrapeGraphAI
        except ImportError as exc:
            raise DragonFruitMeError(
                "DONOR_NOT_INSTALLED",
                "Install the optional managed donor with "
                "pip install 'dragonfruitme[scrapegraph-api]' (Python >= 3.12).",
                recoverable=False,
            ) from exc
        return ScrapeGraphAI()

    def extract(self, *, url: str, fields: list[dict[str, Any]], html: str) -> dict[str, Any]:
        if not html.strip():
            raise DragonFruitMeError("DONOR_INPUT_EMPTY", "ScrapeGraph donor received empty HTML.")
        try:
            result = self._client().extract(
                prompt=_field_prompt(fields),
                html=html,
                schema=_json_schema(fields),
                mode="reader",
            )
        except Exception as exc:
            raise DragonFruitMeError(
                "DONOR_FAILED",
                f"ScrapeGraph API extraction failed ({type(exc).__name__}).",
            ) from exc
        status = getattr(result, "status", None)
        if status is None and isinstance(result, dict):
            status = result.get("status")
        if status != "success":
            raise DragonFruitMeError("DONOR_FAILED", "ScrapeGraph API returned a non-success result.")
        data = getattr(result, "data", None)
        if data is None and isinstance(result, dict):
            data = result.get("data")
        elapsed_ms = getattr(result, "elapsed_ms", None)
        if elapsed_ms is None and isinstance(result, dict):
            elapsed_ms = result.get("elapsed_ms")
        evidence: dict[str, Any] = {"source": "dragonfruit_html", "url": url}
        if isinstance(elapsed_ms, (int, float)):
            evidence["elapsed_ms"] = int(elapsed_ms)
        return {
            "donor": self.name,
            "values": _values_for_fields(data, fields),
            "evidence": evidence,
        }

    def status(self) -> dict[str, Any]:
        installed = self.client_factory is not None or importlib.util.find_spec("scrapegraph_py") is not None
        return {
            "name": self.name,
            "installed": installed,
            "configured": bool(os.environ.get("SGAI_API_KEY", "").strip()) or self.client_factory is not None,
            "data_egress": "scrapegraph_cloud",
        }


def donor_from_env() -> WebDonor | None:
    mode = os.environ.get("DRAGONFRUITME_WEB_DONOR", "").strip().lower()
    if mode in {"", "off", "none", "disabled"}:
        return None
    if mode in {"scrapegraph", "scrapegraph-oss", "oss"}:
        return ScrapeGraphOssDonor()
    if mode in {"scrapegraph-api", "api", "managed"}:
        return ScrapeGraphApiDonor()
    raise DragonFruitMeError(
        "DONOR_CONFIG_INVALID",
        "DRAGONFRUITME_WEB_DONOR must be off, scrapegraph-oss or scrapegraph-api.",
        recoverable=False,
        details={"configured": mode},
    )
