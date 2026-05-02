"""Conformance tests — pipe envelopes through a real happi.md runtime.

These tests run against `bash /Users/lauriescheepers/.hal/happi.md run` if
present, otherwise they skip. The point is end-to-end falsification of the
client/runtime contract: encode an Envelope, write to runtime stdin, parse
NDJSON events from stdout, assert the stream conforms to happi.md.
"""
from __future__ import annotations

import os
import shutil

import pytest

from hal_py import envelope as env_mod
from hal_py.client import sync_dispatch
from hal_py.events import VALID_TYPES

HAPPI_PATH = "/Users/lauriescheepers/.hal/happi.md"
RUNTIME_CMD = ["bash", HAPPI_PATH, "run"]

_runtime_present = os.path.exists(HAPPI_PATH) and shutil.which("bash") is not None
skip_no_runtime = pytest.mark.skipif(
    not _runtime_present,
    reason=f"happi.md runtime not found at {HAPPI_PATH} — conformance test skipped",
)


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
