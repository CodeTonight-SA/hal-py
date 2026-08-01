"""Conformance tests — pipe envelopes through a real happi.md runtime.

End-to-end falsification of the client/runtime contract: encode an Envelope,
write it to a real runtime's stdin, parse the NDJSON events from stdout, and
assert the stream conforms to happi.md.

The runtime is located by tests/_runtime.py rather than a fixed path. These
tests previously hard-coded one developer's home directory, so they skipped
on every other machine — and a skip reports as success, which meant the only
end-to-end checks in this suite had silently never run anywhere else.
"""
from __future__ import annotations

from hal_py import envelope as env_mod
from hal_py.client import sync_dispatch
from hal_py.events import VALID_TYPES

from ._runtime import RUNTIME_CMD, skip_no_runtime


@skip_no_runtime
def test_version_envelope_yields_terminal_event():
    env = env_mod.new("version", id="conformance-1")
    events = sync_dispatch(env, runtime_cmd=RUNTIME_CMD, timeout=15)
    assert events, "runtime produced no events"
    last = events[-1]
    assert last.type in ("completed", "error"), (
        f"stream did not end with terminal event: got {last.type!r}"
    )


@skip_no_runtime
def test_event_ids_echo_envelope_id():
    env = env_mod.new("version", id="conformance-2")
    events = sync_dispatch(env, runtime_cmd=RUNTIME_CMD, timeout=15)
    for event in events:
        assert event.id == "conformance-2", (
            f"event id {event.id!r} does not echo envelope id"
        )


@skip_no_runtime
def test_every_event_type_is_valid():
    env = env_mod.new("version", id="conformance-3")
    events = sync_dispatch(env, runtime_cmd=RUNTIME_CMD, timeout=15)
    for event in events:
        assert event.type in VALID_TYPES, (
            f"runtime emitted unknown event type {event.type!r}"
        )
