"""hal-py — HAPPI/1.3 Python client library.

AI is a syscall. happi.md is the protocol. hal-py is the Python idiom
library: import once, dispatch envelopes, parse events.

Quickstart:

    from hal_py import envelope, sync_dispatch

    env = envelope.new("version", id="hello")
    events = sync_dispatch(env, runtime_cmd=["bash", "/path/to/happi.md", "run"])
    for e in events:
        print(e.type, getattr(e, "text", ""))

Protocol version: this library EMITS `happi/1.3` and ACCEPTS `happi/1.0`
through `happi/1.3`. A runtime stamps events with its OWN version, never the
envelope's, so a v1.0 request to a v1.3 runtime yields v1.3 events — accepting
the whole range is what makes the client interoperable rather than brittle.

Public API:

    envelope.Envelope, envelope.new, envelope.from_json, envelope.from_dict
    events.Event + 9 typed subclasses, events.parse_stream
    cite.new, cite.provenance_from_events   (v1.3 citation provenance)
    client.sync_dispatch, client.async_dispatch
    providers.dispatch_anthropic

Reference: https://gist.github.com/LaurieScheepers/2483d5c218c21ecc931130bcee7dee83
"""
from . import cite, envelope, events, happi, providers
from .cite import Citation, CiteError, Provenance, Source
from .client import DispatchError, async_dispatch, sync_dispatch
from .envelope import ACCEPTED_VERSIONS, Envelope, EnvelopeError, HAPPI_VERSION
from .events import (
    CompletedEvent,
    ContextEvent,
    DeltaEvent,
    ErrorEvent,
    Event,
    EventError,
    IdrEvent,
    StartedEvent,
    SubRequestEvent,
    UnknownEvent,
    ToolCallEvent,
    ToolResultEvent,
    is_post_terminator,
    is_outcome,
    is_terminal,
)

__version__ = "0.3.0"

__all__ = [
    "HAPPI_VERSION",
    "ACCEPTED_VERSIONS",
    "happi",
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
    "UnknownEvent",
    "CompletedEvent",
    "ErrorEvent",
    "IdrEvent",
    "ContextEvent",
    "is_outcome",
    "is_terminal",
    "is_post_terminator",
    "events",
    # Citation provenance (v1.3)
    "cite",
    "Source",
    "Citation",
    "Provenance",
    "CiteError",
    # Client
    "sync_dispatch",
    "async_dispatch",
    "DispatchError",
    # Providers
    "providers",
]
