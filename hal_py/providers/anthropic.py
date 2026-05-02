"""Anthropic adapter — maps HAPPI envelope -> messages.create -> events.

This is a HAPPI/1.0 provider adapter. It does NOT implement the runtime
itself — it produces the same NDJSON event stream a runtime would emit, so
callers can use it as a drop-in for `bash happi.md run` when targeting
Anthropic's Claude models specifically.

v0.1 ships non-streaming only: one envelope in -> started + one delta +
completed (or error). Streaming SSE is v0.2 (mapped to multiple deltas).

Stdlib-only: no anthropic SDK dependency. The doctrine says return shapes
must not leak provider-specific types; we re-emit standardised events.

Token resolution: per the HAPPI/1.0 auth scheme (whitepaper §1.2), the
adapter reads `auth.token` from the envelope. Three forms are accepted:

    "env:VAR_NAME"      — read named environment variable
    "keychain:SERVICE"  — caller pre-resolves; literal required at dispatch
    literal string      — used as-is

There is no hardcoded fallback environment variable. Callers wishing to
use a default env var should set `auth.token = "env:THEIR_VAR_NAME"`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from ..envelope import Envelope
from ..events import (
    CompletedEvent,
    DeltaEvent,
    ErrorEvent,
    Event,
    HAPPI_VERSION,
    StartedEvent,
)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-3-5-sonnet-latest"
DEFAULT_MAX_TOKENS = 1024


def _resolve_token(envelope: Envelope) -> str:
    """Return the API token. Supports env:VAR, keychain:SVC, or literal.

    Raises ValueError if no auth.token is set or if a keychain ref is
    given (callers must pre-resolve keychain entries).
    """
    auth = envelope.auth or {}
    raw = auth.get("token", "")
    if not isinstance(raw, str) or not raw:
        raise ValueError("envelope.auth.token is required")
    if raw.startswith("env:"):
        var = raw[len("env:"):]
        return os.environ.get(var, "")
    if raw.startswith("keychain:"):
        raise ValueError("keychain: tokens must be pre-resolved by the caller")
    return raw


def _build_request_body(envelope: Envelope) -> dict[str, Any]:
    """Build the Anthropic messages.create request body from a HAPPI envelope.

    Convention (mirrors §1.1 of the possibilities whitepaper):
      args[0] is a dict containing model/messages/etc., OR
      flags carry model + messages directly.
    """
    args = envelope.args or []
    flags = envelope.flags or {}
    body: dict[str, Any] = {}
    if args and isinstance(args[0], dict):
        body.update(args[0])
    body.setdefault("model", flags.get("model", DEFAULT_MODEL))
    body.setdefault("max_tokens", flags.get("max_tokens", DEFAULT_MAX_TOKENS))
    if "messages" not in body:
        body["messages"] = flags.get("messages", [])
    return body


def _post_messages(body: dict[str, Any], token: str,
                   timeout: float | None) -> dict[str, Any]:
    """POST to Anthropic /v1/messages and return the parsed JSON response."""
    if not token:
        raise ValueError("auth token resolution returned empty value")
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        method="POST",
        headers={
            "x-api-key": token,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_text(response: dict[str, Any]) -> str:
    """Concatenate text blocks from an Anthropic messages response."""
    chunks: list[str] = []
    for block in response.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(block.get("text", ""))
    return "".join(chunks)


def _ts_provider(start_ns: int) -> int:
    """Milliseconds since dispatch began (used for ts on every event)."""
    return max(0, (time.time_ns() - start_ns) // 1_000_000)


def _build_started(env_id: str, ts: int) -> StartedEvent:
    return StartedEvent(v=HAPPI_VERSION, id=env_id, ts=ts)


def _build_delta(env_id: str, ts: int, text: str) -> DeltaEvent:
    return DeltaEvent(v=HAPPI_VERSION, id=env_id, ts=ts, text=text)


def _build_completed(env_id: str, ts: int,
                     usage: dict[str, Any] | None) -> CompletedEvent:
    return CompletedEvent(v=HAPPI_VERSION, id=env_id, ts=ts, usage=usage)


def _build_error(env_id: str, ts: int, code: str, message: str) -> ErrorEvent:
    return ErrorEvent(v=HAPPI_VERSION, id=env_id, ts=ts,
                      code=code, message=message)


def dispatch_anthropic(envelope: Envelope,
                       timeout: float | None = None) -> list[Event]:
    """Dispatch a HAPPI envelope to Anthropic and return the event list.

    Always emits started first. On success: one delta with the response
    text + completed with usage. On failure: error with classified code.
    Mirrors the seven-event-type contract (happi.md axiom 2).
    """
    start_ns = time.time_ns()
    events: list[Event] = [_build_started(envelope.id, _ts_provider(start_ns))]
    try:
        token = _resolve_token(envelope)
        body = _build_request_body(envelope)
        response = _post_messages(body, token, timeout)
    except ValueError as exc:
        events.append(_build_error(envelope.id, _ts_provider(start_ns),
                                   "auth_error", str(exc)))
        return events
    except urllib.error.HTTPError as exc:
        events.append(_build_error(envelope.id, _ts_provider(start_ns),
                                   "runtime_error",
                                   f"HTTP {exc.code}: {exc.reason}"))
        return events
    except Exception as exc:  # noqa: BLE001
        events.append(_build_error(envelope.id, _ts_provider(start_ns),
                                   "runtime_error",
                                   f"{type(exc).__name__}: {exc}"))
        return events

    text = _extract_text(response)
    if text:
        events.append(_build_delta(envelope.id, _ts_provider(start_ns), text))
    events.append(_build_completed(envelope.id, _ts_provider(start_ns),
                                   response.get("usage")))
    return events
