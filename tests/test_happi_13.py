"""HAPPI/1.3 conformance — version negotiation, v1.1 terminators, cite.verify.

These are the regression anchors for a real interop break: hal-py v0.1 pinned
its version check to exact equality with "happi/1.0", so the moment the
reference runtime advanced to happi/1.3 the library rejected EVERY event it
produced ("unsupported version: 'happi/1.3'"). A runtime always stamps events
with its own version, never the envelope's, so exact equality can only ever
work against one runtime build.

The load-bearing test is test_live_reference_runtime_round_trip: it dispatches
against the actual happi.md and asserts the stream parses. It is skipped when
no runtime is reachable, so CI without HAL still runs the rest.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from hal_py import cite, envelope, events
from hal_py.client import DispatchError, sync_dispatch
from hal_py.envelope import ACCEPTED_VERSIONS, EnvelopeError
from hal_py.events import (
    ContextEvent,
    EventError,
    IdrEvent,
    from_json_line,
    is_post_terminator,
    is_terminal,
    parse_stream,
)


# ---------------------------------------------------------------------------
# Version negotiation — the regression anchor for the v1.0-pinning break
# ---------------------------------------------------------------------------

ALL_VERSIONS = ("happi/1.0", "happi/1.1", "happi/1.2", "happi/1.3")


def test_emitted_version_is_1_3():
    assert envelope.HAPPI_VERSION == "happi/1.3"
    assert events.HAPPI_VERSION == "happi/1.3"


def test_accepted_versions_span_1_0_to_1_3():
    # Named explicitly rather than compared to the module constant, so a
    # mutation that narrows the tuple fails instead of trivially agreeing.
    assert set(ACCEPTED_VERSIONS) == set(ALL_VERSIONS)
    assert set(events.ACCEPTED_VERSIONS) == set(ALL_VERSIONS)


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_envelope_accepts_every_supported_version(version):
    env = envelope.from_dict({"v": version, "id": "r", "cmd": "version"})
    assert env.v == version  # round-trips unchanged, never rewritten


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_events_accept_every_supported_version(version):
    line = json.dumps({"v": version, "id": "r", "type": "started", "ts": 0})
    assert from_json_line(line).v == version


def test_event_stream_from_a_1_3_runtime_parses():
    # Verbatim shape of what `bash happi.md run` emits for cmd `version`.
    stream = (
        '{"v":"happi/1.3","id":"probe","type":"started","ts":0}\n'
        '{"v":"happi/1.3","id":"probe","type":"delta","ts":0,"text":"happi/1.3"}\n'
        '{"v":"happi/1.3","id":"probe","type":"completed","ts":0}\n'
    )
    parsed = list(parse_stream(stream))
    assert [e.type for e in parsed] == ["started", "delta", "completed"]
    assert parsed[1].text == "happi/1.3"


@pytest.mark.parametrize("future", ["happi/1.4", "happi/1.5", "happi/1.12",
                                    "happi/1.99"])
def test_future_minors_of_the_same_major_are_accepted(future):
    """A minor this library has never heard of must still parse.

    A runtime stamps events with ITS OWN version, so a client will meet
    versions newer than itself as a matter of course. This test previously
    asserted the opposite — that happi/1.4 must be REJECTED — which encoded
    the old fixed-enumeration policy. Under that policy the first conforming
    happi/1.4 runtime to ship would have had its entire event stream dropped
    by every released version of this library, and silently: a decoder that
    refuses a version returns nothing rather than raising something visible
    to the end user.
    """
    assert envelope.from_dict({"v": future, "id": "r", "cmd": "version"}).v == future
    line = json.dumps({"v": future, "id": "r", "type": "started", "ts": 0})
    assert from_json_line(line).v == future


@pytest.mark.parametrize("bad", ["happi/0.9", "happi/2.0", "happi/2.1", "",
                                 "1.3", "happi/", "happi/1", "happi/x.y",
                                 "HAPPI/1.3", None])
def test_other_majors_and_malformed_versions_are_rejected(bad):
    """Widening to happi/1.x must not become "accept anything".

    A MAJOR bump is the signal for a breaking change, so it is refused. So is
    anything that is not a parseable happi/N.M string — otherwise the policy
    would admit garbage rather than merely admitting the future.
    """
    with pytest.raises(EnvelopeError):
        envelope.from_dict({"v": bad, "id": "r", "cmd": "version"})
    with pytest.raises(EventError):
        from_json_line(json.dumps({"v": bad, "id": "r", "type": "started", "ts": 0}))


# ---------------------------------------------------------------------------
# v1.1 terminators — idr + context
# ---------------------------------------------------------------------------

IDR_LINE = json.dumps({
    "v": "happi/1.3", "id": "r", "type": "idr", "ts": 7,
    "idr_ref": {"sha256": "abc123", "cid": None,
                "model_versions": ["claude-opus-4-7"], "block_anchor": None},
})

CONTEXT_LINE = json.dumps({
    "v": "happi/1.3", "id": "r", "type": "context", "ts": 8,
    "context_ref": {"sha256": "sha256:def456", "predecessor_context": "r-prev",
                    "snapshot_ref": None, "kind": "context-delta",
                    "model_versions": ["claude-opus-4-7"]},
})


def test_idr_event_parses_into_typed_subclass():
    event = from_json_line(IDR_LINE)
    assert isinstance(event, IdrEvent)
    assert event.idr_ref["sha256"] == "abc123"
    assert event.idr_ref["model_versions"] == ["claude-opus-4-7"]


def test_context_event_parses_into_typed_subclass():
    event = from_json_line(CONTEXT_LINE)
    assert isinstance(event, ContextEvent)
    assert event.context_ref["kind"] == "context-delta"
    assert event.context_ref["predecessor_context"] == "r-prev"


def test_idr_requires_idr_ref():
    with pytest.raises(EventError):
        from_json_line('{"v":"happi/1.3","id":"r","type":"idr","ts":1}')


def test_context_requires_context_ref():
    with pytest.raises(EventError):
        from_json_line('{"v":"happi/1.3","id":"r","type":"context","ts":1}')


def test_terminator_classification():
    outcome = from_json_line(
        '{"v":"happi/1.3","id":"r","type":"completed","ts":5}')
    idr = from_json_line(IDR_LINE)
    assert is_terminal(outcome) and not is_post_terminator(outcome)
    assert is_post_terminator(idr) and not is_terminal(idr)


def test_audited_stream_ending_in_idr_is_not_a_protocol_violation():
    # v0.1's client asserted events[-1] was completed/error, so an audited
    # run (…completed, idr) raised DispatchError. This is that anchor.
    from hal_py.client import _check_terminal
    stream = (
        '{"v":"happi/1.3","id":"r","type":"started","ts":0}\n'
        '{"v":"happi/1.3","id":"r","type":"completed","ts":5}\n'
        + IDR_LINE + "\n"
    )
    _check_terminal(list(parse_stream(stream)), 0, "")  # must not raise


def test_stream_with_no_outcome_event_still_raises():
    from hal_py.client import _check_terminal
    stream = '{"v":"happi/1.3","id":"r","type":"started","ts":0}\n'
    with pytest.raises(DispatchError):
        _check_terminal(list(parse_stream(stream)), 0, "")


def test_streaming_event_after_outcome_still_raises():
    # Only idr/context may trail the outcome — a delta after completed is
    # still a protocol violation and must not be waved through.
    from hal_py.client import _check_terminal
    stream = (
        '{"v":"happi/1.3","id":"r","type":"completed","ts":5}\n'
        '{"v":"happi/1.3","id":"r","type":"delta","ts":6,"text":"late"}\n'
    )
    with pytest.raises(DispatchError):
        _check_terminal(list(parse_stream(stream)), 0, "")


# ---------------------------------------------------------------------------
# v1.3 cite.verify
# ---------------------------------------------------------------------------

SOURCE = cite.Source("s1", "The Limitation Act 1980 bars the claim after six years.")
REAL = cite.Citation("c1", "s1", "bars the claim after six years")
FAKE = cite.Citation("c2", "s1", "bars the claim after three years")


def test_cite_verify_envelope_shape():
    env = cite.new([SOURCE], [REAL, FAKE], id="cv-1")
    assert env.cmd == "cite.verify"
    assert env.v == "happi/1.3"
    assert env.flags["sources"] == [{"id": "s1", "text": SOURCE.text}]
    assert env.flags["citations"][1]["quote"] == FAKE.quote
    assert "strict" not in env.flags  # default off — caller reads grounding_rate


def test_cite_verify_strict_sets_flag():
    env = cite.new([SOURCE], [REAL], id="cv-2", strict=True)
    assert env.flags["strict"] is True


def test_cite_verify_rejects_empty_inputs():
    with pytest.raises(cite.CiteError):
        cite.new([], [REAL], id="cv-3")
    with pytest.raises(cite.CiteError):
        cite.new([SOURCE], [], id="cv-4")


def test_provenance_parses_and_separates_fabricated_citations():
    usage = {
        "sources": {"s1": {"sha256": "deadbeef", "chars": 55}},
        "citations": [
            {"id": "c1", "source_id": "s1", "status": "verified",
             "start": 24, "end": 54},
            {"id": "c2", "source_id": "s1", "status": "not_found",
             "start": -1, "end": -1},
        ],
        "tally": {"verified": 1, "fuzzy": 0, "not_found": 1},
        "grounding_rate": 0.5,
    }
    record = cite.provenance_from_usage(usage)
    assert record.grounding_rate == 0.5
    assert [c.id for c in record.verified] == ["c1"]
    assert [c.id for c in record.not_found] == ["c2"]
    assert record.verified[0].grounded is True
    assert record.not_found[0].grounded is False
    assert record.is_fully_grounded is False


def test_fully_grounded_requires_at_least_one_citation():
    # An empty record proves nothing, so it must not report as grounded.
    assert cite.Provenance().is_fully_grounded is False


def test_provenance_rejects_unknown_status():
    with pytest.raises(cite.CiteError):
        cite.provenance_from_usage(
            {"citations": [{"id": "c1", "source_id": "s1", "status": "probably"}]})


# ---------------------------------------------------------------------------
# Live conformance against the reference runtime (skipped when unavailable)
# ---------------------------------------------------------------------------

def _reference_runtime() -> list[str] | None:
    """Locate a runnable happi.md, or None."""
    candidate = os.environ.get("HAPPI_MD")
    if not candidate:
        for path in (Path.home() / ".hal" / "happi.md",
                     Path.home() / ".hal" / "HAPPI.md"):
            if path.is_file():
                candidate = str(path)
                break
    if not candidate or not Path(candidate).is_file():
        return None
    if shutil.which("bash") is None:
        return None
    return ["bash", candidate, "run"]


requires_runtime = pytest.mark.skipif(
    _reference_runtime() is None,
    reason="reference happi.md not found (set HAPPI_MD=/path/to/happi.md)",
)


@requires_runtime
def test_live_reference_runtime_round_trip():
    """The end-to-end anchor: dispatch a v1.0 envelope, parse a v1.3 stream.

    This is the exact interop that hal-py v0.1 could not do.
    """
    env = envelope.Envelope(v="happi/1.0", id="live-1", cmd="version")
    parsed = sync_dispatch(env, runtime_cmd=_reference_runtime())
    assert [e.type for e in parsed] == ["started", "delta", "completed"]
    # The runtime reports ITS version, not the envelope's.
    assert parsed[1].text.startswith("happi/1.")


@requires_runtime
def test_live_cite_verify_catches_a_fabricated_quote():
    """A fabricated citation must resolve to not_found — arithmetic, not opinion."""
    env = cite.new([SOURCE], [REAL, FAKE], id="live-cv")
    record = cite.provenance_from_events(
        sync_dispatch(env, runtime_cmd=_reference_runtime()))
    assert [c.id for c in record.not_found] == ["c2"]
    assert record.citations[0].grounded is True
    assert record.grounding_rate == 0.5
