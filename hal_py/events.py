"""HAPPI/1.3 events — the seven streaming types plus two terminators.

Per happi.md axiom 2: seven event types are sufficient for streaming. Every
event carries v, id (echoes envelope id), type, ts (ms integer). Per-type
fields:

    started      — (none)
    delta        — text
    tool_call    — name, input, call_id
    tool_result  — name, output, call_id
    sub_request  — envelope
    completed    — usage (optional)
    error        — code, message

v1.1 added two non-streaming terminators, emitted AFTER completed/error:

    idr          — idr_ref      (audit receipt: "what happened in this run?")
    context      — context_ref  (memory-chain link: "what is this belief?")

Version handling follows happi.md: this library ACCEPTS envelopes and events
carrying any version in ACCEPTED_VERSIONS ("happi/1.0".."happi/1.3") and EMITS
HAPPI_VERSION. A runtime always stamps events with its own version, never the
envelope's — so a v1.0 request to a v1.3 runtime yields v1.3 events, and a
strict equality check would reject a perfectly valid stream.

Provider-specific data lives under provider.* sub-fields, which round-trip
through the typed parser via the `extra` dict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from . import happi

#: Protocol version this library emits.
HAPPI_VERSION = happi.VERSION

#: Protocol versions that existed when this module was written. Informational
#: only — acceptance is decided by happi.is_accepted, which admits any
#: happi/1.x. This matters most here: a runtime stamps events with its OWN
#: version, so a client meeting a newer runtime sees versions this library has
#: never heard of. Rejecting them would silently drop the entire stream.
ACCEPTED_VERSIONS = happi.KNOWN_VERSIONS

VALID_TYPES = (
    "started",
    "delta",
    "tool_call",
    "tool_result",
    "sub_request",
    "completed",
    "error",
    "idr",
    "context",
)

#: Valid `context_ref.kind` values per happi.md CONTEXT section.
CONTEXT_KINDS = ("context-delta", "context-snapshot", "context-supersede")

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


@dataclass
class IdrEvent(Event):
    """Intent Decision Record — the audit receipt for one dispatch (v1.1).

    `idr_ref` carries the content hash of (envelope JSON + event NDJSON):
    sha256 (required), model_versions (required), cid and block_anchor
    (optional). Emitted after completed/error when flags.audit=true, or
    explicitly via cmd `idr.emit`.
    """

    type: str = "idr"
    idr_ref: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextEvent(Event):
    """Replayable memory-chain link — the structural twin of `idr` (v1.1).

    `context_ref` content-addresses the *decision body* (excluding the
    volatile id/ts/audit fields), so two records with identical semantic
    content share one sha256 — the dedup/merkle coordinate. Required:
    sha256, kind, model_versions. Optional: predecessor_context,
    snapshot_ref. Emitted via cmd `context.append`.
    """

    type: str = "context"
    context_ref: dict[str, Any] = field(default_factory=dict)


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
    "idr": (IdrEvent, ("idr_ref",)),
    "context": (ContextEvent, ("context_ref",)),
}

# Fields shared by every event — already extracted before dispatch.
_BASE_FIELDS = ("v", "id", "type", "ts")


def _validate_base(data: dict[str, Any]) -> None:
    """Raise EventError if base fields are wrong/missing."""
    if not happi.is_accepted(data.get("v")):
        raise EventError(
            f"unsupported version: {data.get('v')!r} "
            f"(accepted: {happi.accepted_list()})"
        )
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


#: Events that report the OUTCOME of a dispatch. Exactly one per stream.
OUTCOME_TYPES = ("completed", "error")

#: Non-streaming records emitted AFTER the outcome event (v1.1). A stream may
#: carry at most one of each; they do not replace the outcome event.
POST_TERMINATOR_TYPES = ("idr", "context")


def is_terminal(event: Event) -> bool:
    """Return True if this event reports the dispatch outcome.

    Note (v1.1+): the outcome event is NOT always the LAST event. When
    flags.audit=true the runtime appends an `idr` record after it, and
    cmd `context.append` appends a `context` record. Use is_post_terminator
    to recognise those; do not treat their presence as a protocol violation.
    """
    return event.type in OUTCOME_TYPES


def is_post_terminator(event: Event) -> bool:
    """Return True for records emitted after the outcome event (idr/context)."""
    return event.type in POST_TERMINATOR_TYPES
