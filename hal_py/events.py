"""HAPPI/1.0 events — the seven event types.

Per happi.md axiom 2: seven event types are sufficient. Every event carries
v, id (echoes envelope id), type, ts (ms integer). Per-type fields:

    started      — (none)
    delta        — text
    tool_call    — name, input, call_id
    tool_result  — name, output, call_id
    sub_request  — envelope
    completed    — usage (optional)
    error        — code, message

Provider-specific data lives under provider.* sub-fields, which round-trip
through the typed parser via the `extra` dict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

HAPPI_VERSION = "happi/1.0"

VALID_TYPES = (
    "started",
    "delta",
    "tool_call",
    "tool_result",
    "sub_request",
    "completed",
    "error",
)

# Standard error codes per happi.md.
ERROR_CODES = ("parse_error", "unsupported_cmd", "auth_error", "runtime_error")


class EventError(ValueError):
    """Raised when an event line fails schema validation."""


@dataclass
class Event:
    """Base event. All events carry v, id, type, ts; subclasses add fields."""

    id: str = ""
    type: str = ""
    ts: int = 0
    v: str = HAPPI_VERSION
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class StartedEvent(Event):
    type: str = "started"


@dataclass
class DeltaEvent(Event):
    type: str = "delta"
    text: str = ""


@dataclass
class ToolCallEvent(Event):
    type: str = "tool_call"
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolResultEvent(Event):
    type: str = "tool_result"
    name: str = ""
    output: Any = None
    call_id: str = ""


@dataclass
class SubRequestEvent(Event):
    type: str = "sub_request"
    envelope: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletedEvent(Event):
    type: str = "completed"
    usage: dict[str, Any] | None = None


@dataclass
class ErrorEvent(Event):
    type: str = "error"
    code: str = ""
    message: str = ""


# Dispatch table: type-string -> (class, required-fields)
# Keeps from_dict's CC low by avoiding nested if/elif chains.
_DISPATCH: dict[str, tuple[type, tuple[str, ...]]] = {
    "started": (StartedEvent, ()),
    "delta": (DeltaEvent, ("text",)),
    "tool_call": (ToolCallEvent, ("name", "input", "call_id")),
    "tool_result": (ToolResultEvent, ("name", "output", "call_id")),
    "sub_request": (SubRequestEvent, ("envelope",)),
    "completed": (CompletedEvent, ()),
    "error": (ErrorEvent, ("code", "message")),
}

# Fields shared by every event — already extracted before dispatch.
_BASE_FIELDS = ("v", "id", "type", "ts")


def _validate_base(data: dict[str, Any]) -> None:
    """Raise EventError if base fields are wrong/missing."""
    if data.get("v") != HAPPI_VERSION:
        raise EventError(f"unsupported version: {data.get('v')!r}")
    if not isinstance(data.get("id"), str) or not data["id"]:
        raise EventError("missing or invalid event 'id'")
    etype = data.get("type")
    if etype not in VALID_TYPES:
        raise EventError(f"unknown event type: {etype!r}")
    if not isinstance(data.get("ts"), int) or data["ts"] < 0:
        raise EventError("missing or invalid 'ts' (must be non-negative int)")


def _check_required(data: dict[str, Any], required: Iterable[str]) -> None:
    """Raise EventError if a per-type required field is missing."""
    for fld in required:
        if fld not in data:
            raise EventError(f"event type {data['type']!r} missing field {fld!r}")


def from_dict(data: dict[str, Any]) -> Event:
    """Build the right typed Event subclass from a dict."""
    if not isinstance(data, dict):
        raise EventError("event must be a JSON object")
    _validate_base(data)
    cls, required = _DISPATCH[data["type"]]
    _check_required(data, required)
    extra = {k: v for k, v in data.items()
             if k not in _BASE_FIELDS and k not in required and k != "usage"}
    kwargs = {fld: data[fld] for fld in required}
    if data["type"] == "completed" and "usage" in data:
        kwargs["usage"] = data["usage"]
    return cls(v=data["v"], id=data["id"], ts=data["ts"], extra=extra, **kwargs)


def from_json_line(line: str) -> Event:
    """Parse one NDJSON line into a typed Event."""
    line = line.strip()
    if not line:
        raise EventError("empty event line")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise EventError(f"invalid JSON: {exc}") from exc
    return from_dict(data)


def parse_stream(text: str) -> Iterator[Event]:
    """Iterate over events in an NDJSON stream (string)."""
    for line in text.splitlines():
        if line.strip():
            yield from_json_line(line)


def is_terminal(event: Event) -> bool:
    """Return True if this event ends the stream (completed or error)."""
    return event.type in ("completed", "error")
