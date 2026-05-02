"""HAPPI/1.0 envelope — encode, decode, validate.

The envelope is the canonical input to a HAPPI runtime. One JSON envelope
in (stdin), one NDJSON event stream out (stdout). The schema is defined in
the OpenAPI section of happi.md (additionalProperties: true — unknown
fields round-trip unchanged).

Per the syscall doctrine, this module owns the wire-format. No provider
specifics, no transport specifics. Just envelope <-> dict <-> JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

HAPPI_VERSION = "happi/1.0"

# Reserved fields per happi.md envelope schema. Anything else round-trips
# under the additionalProperties: true contract.
_KNOWN_FIELDS = ("v", "id", "cmd", "args", "flags", "auth")


class EnvelopeError(ValueError):
    """Raised when an envelope fails schema validation."""


@dataclass
class Envelope:
    """A HAPPI/1.0 envelope.

    Required fields: v, id, cmd. Optional: args (list), flags (dict), auth (dict).
    Unknown fields are preserved in `extra` and serialised back unchanged.
    """

    id: str
    cmd: str
    v: str = HAPPI_VERSION
    args: list[Any] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict matching the envelope schema."""
        out: dict[str, Any] = {"v": self.v, "id": self.id, "cmd": self.cmd}
        if self.args:
            out["args"] = list(self.args)
        if self.flags:
            out["flags"] = dict(self.flags)
        if self.auth:
            out["auth"] = dict(self.auth)
        # Unknown fields round-trip under additionalProperties: true.
        for k, val in self.extra.items():
            out[k] = val
        return out

    def to_json(self) -> str:
        """Serialise to compact JSON (one line, no trailing newline)."""
        return json.dumps(self.to_dict(), separators=(",", ":"))


def _validate_required(env: dict[str, Any]) -> None:
    """Raise EnvelopeError if required fields are missing or malformed."""
    if env.get("v") != HAPPI_VERSION:
        raise EnvelopeError(f"unsupported version: {env.get('v')!r}")
    eid = env.get("id")
    if not isinstance(eid, str) or not eid:
        raise EnvelopeError("missing or invalid 'id' (must be non-empty string)")
    cmd = env.get("cmd")
    if not isinstance(cmd, str) or not cmd:
        raise EnvelopeError("missing or invalid 'cmd' (must be non-empty string)")


def _validate_optional(env: dict[str, Any]) -> None:
    """Raise EnvelopeError if optional fields have wrong type when present."""
    if "args" in env and not isinstance(env["args"], list):
        raise EnvelopeError("'args' must be an array when present")
    if "flags" in env and not isinstance(env["flags"], dict):
        raise EnvelopeError("'flags' must be an object when present")
    if "auth" in env and not isinstance(env["auth"], dict):
        raise EnvelopeError("'auth' must be an object when present")


def from_dict(data: dict[str, Any]) -> Envelope:
    """Build an Envelope from a dict, validating against the HAPPI schema."""
    if not isinstance(data, dict):
        raise EnvelopeError("envelope must be a JSON object")
    _validate_required(data)
    _validate_optional(data)
    extra = {k: v for k, v in data.items() if k not in _KNOWN_FIELDS}
    return Envelope(
        v=data["v"],
        id=data["id"],
        cmd=data["cmd"],
        args=list(data.get("args", [])),
        flags=dict(data.get("flags", {})),
        auth=dict(data.get("auth", {})),
        extra=extra,
    )


def from_json(raw: str) -> Envelope:
    """Parse a JSON envelope from a string."""
    if not raw or not raw.strip():
        raise EnvelopeError("empty input")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"invalid JSON: {exc}") from exc
    return from_dict(data)


def new(cmd: str, *, id: str, args: list[Any] | None = None,  # noqa: A002
        flags: dict[str, Any] | None = None,
        auth: dict[str, Any] | None = None) -> Envelope:
    """Convenience constructor: `envelope.new("version", id="hello")`."""
    return Envelope(
        id=id,
        cmd=cmd,
        args=list(args or []),
        flags=dict(flags or {}),
        auth=dict(auth or {}),
    )
