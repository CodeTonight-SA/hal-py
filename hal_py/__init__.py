"""hal-py — HAPPI/1.0 Python client library.

AI is a syscall. happi.md is the protocol. hal-py is the Python idiom
library: import once, dispatch envelopes, parse events.

Quickstart:

    from hal_py import envelope, sync_dispatch

    env = envelope.new("version", id="hello")
    events = sync_dispatch(env, runtime_cmd=["bash", "/path/to/happi.md", "run"])
    for e in events:
        print(e.type, getattr(e, "text", ""))

Public API:

    envelope.Envelope, envelope.new, envelope.from_json, envelope.from_dict
    events.Event + 7 typed subclasses, events.parse_stream
    client.sync_dispatch, client.async_dispatch
    providers.dispatch_anthropic

Reference: https://github.com/CodeTonight-SA/HAL/blob/main/happi.md
"""
from . import envelope, events, providers
from .client import DispatchError, async_dispatch, sync_dispatch
from .envelope import Envelope, EnvelopeError, HAPPI_VERSION
from .events import (
    CompletedEvent,
    DeltaEvent,
    ErrorEvent,
    Event,
    EventError,
    StartedEvent,
    SubRequestEvent,
    ToolCallEvent,
    ToolResultEvent,
)

__version__ = "0.1.0"

__all__ = [
    "HAPPI_VERSION",
    "__version__",
    # Envelope
    "Envelope",
    "EnvelopeError",
    "envelope",
    # Events
    "Event",
    "EventError",
    "StartedEvent",
    "DeltaEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "SubRequestEvent",
    "CompletedEvent",
    "ErrorEvent",
    "events",
    # Client
    "sync_dispatch",
    "async_dispatch",
    "DispatchError",
    # Providers
    "providers",
]
