"""Tests for the hal-py HAPPI runtime (`python3 -m hal_py.run`).

These drive the runtime as a subprocess wherever the property under test is
about the process contract (exit codes, stdout being a clean NDJSON stream),
and call into it directly where the property is about a pure function. Testing
a runtime only through in-process calls would miss the two failure modes that
actually bite a caller: a non-zero exit that should be zero, and stray output
on stdout corrupting the event stream.
"""
from __future__ import annotations

import json
import subprocess
import time
import sys
from pathlib import Path

import pytest

from hal_py import happi
from hal_py.run import content_addr, dispatch, verify_quote

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_envelope(env: dict) -> tuple[list[dict], int]:
    """Dispatch one envelope through the real CLI entry point."""
    proc = subprocess.run(
        [sys.executable, "-m", "hal_py.run"],
        input=json.dumps(env),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
        check=False,
    )
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return events, proc.returncode


def types_of(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# process contract
# ---------------------------------------------------------------------------

def test_every_line_on_stdout_is_a_json_event():
    """stdout is the event stream and nothing else.

    A stray print, banner, or warning on stdout does not fail any assertion
    about event content, but it does break every consumer, because the reader
    parses stdout line by line.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "hal_py.run"],
        input='{"v":"happi/1.3","id":"x","cmd":"spec.describe"}',
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=30, check=False,
    )
    assert proc.returncode == 0
    for line in proc.stdout.splitlines():
        if line.strip():
            json.loads(line)  # raises if anything non-JSON reached stdout


def test_success_exits_zero_and_failure_exits_non_zero():
    _, ok_code = run_envelope({"v": "happi/1.3", "id": "a", "cmd": "version"})
    assert ok_code == 0
    _, bad_code = run_envelope({"v": "happi/1.3", "id": "b", "cmd": "no.such.cmd"})
    assert bad_code != 0


def test_unknown_command_reports_unsupported_cmd():
    events, code = run_envelope({"v": "happi/1.3", "id": "b", "cmd": "no.such.cmd"})
    assert code != 0
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "unsupported_cmd"


def test_malformed_stdin_is_a_parse_error_not_a_traceback():
    proc = subprocess.run(
        [sys.executable, "-m", "hal_py.run"],
        input="{not json", capture_output=True, text=True,
        cwd=REPO_ROOT, timeout=30, check=False,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    events = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "parse_error"


# ---------------------------------------------------------------------------
# version handling
# ---------------------------------------------------------------------------

def test_runtime_reports_its_own_version_not_the_envelopes():
    """The single most important version property.

    Every one of the three SDK breakages this release fixes traced back to
    assuming the reply carries the version that was sent.
    """
    events, code = run_envelope({"v": "happi/1.0", "id": "v", "cmd": "version"})
    assert code == 0
    assert {e["v"] for e in events} == {happi.VERSION}
    deltas = [e for e in events if e["type"] == "delta"]
    assert deltas[0]["text"] == happi.VERSION


@pytest.mark.parametrize("version", ["happi/1.0", "happi/1.1", "happi/1.2",
                                     "happi/1.3", "happi/1.7"])
def test_accepts_any_minor_of_the_supported_major(version):
    events, code = run_envelope({"v": version, "id": "v", "cmd": "version"})
    assert code == 0
    assert types_of(events)[-1] == "completed"


@pytest.mark.parametrize("version", ["happi/0.9", "happi/2.0", "nonsense"])
def test_rejects_other_majors_and_junk(version):
    events, code = run_envelope({"v": version, "id": "v", "cmd": "version"})
    assert code != 0
    assert types_of(events) == ["error"]


# ---------------------------------------------------------------------------
# stream shape
# ---------------------------------------------------------------------------

def test_echo_streams_one_delta_per_argument():
    events, code = run_envelope({"v": "happi/1.3", "id": "e", "cmd": "echo",
                                 "args": ["one", "two", "three"]})
    assert code == 0
    assert [e["text"] for e in events if e["type"] == "delta"] == \
        ["one", "two", "three"]


def test_exactly_one_outcome_event_per_stream():
    events, _ = run_envelope({"v": "happi/1.3", "id": "o", "cmd": "echo",
                              "args": ["x"]})
    outcomes = [t for t in types_of(events) if t in ("completed", "error")]
    assert len(outcomes) == 1


def test_audit_flag_appends_an_idr_after_the_outcome():
    events, code = run_envelope({
        "v": "happi/1.1", "id": "a", "cmd": "echo", "args": ["x"],
        "flags": {"audit": True, "model_versions": ["test-model"]},
    })
    assert code == 0
    kinds = types_of(events)
    assert kinds[-1] == "idr", "idr must be the terminator, after the outcome"
    assert kinds.index("completed") < kinds.index("idr")
    assert events[-1]["idr_ref"]["model_versions"] == ["test-model"]


def test_idr_hash_is_deterministic_for_the_same_envelope():
    env = {"v": "happi/1.1", "id": "a", "cmd": "echo", "args": ["x"],
           "flags": {"audit": True, "model_versions": ["m"]}}
    first, _ = run_envelope(env)
    second, _ = run_envelope(env)
    assert first[-1]["idr_ref"]["sha256"] == second[-1]["idr_ref"]["sha256"]


def test_no_idr_when_audit_is_not_requested():
    events, _ = run_envelope({"v": "happi/1.3", "id": "a", "cmd": "echo",
                              "args": ["x"]})
    assert "idr" not in types_of(events)


# ---------------------------------------------------------------------------
# context.append
# ---------------------------------------------------------------------------

def test_context_is_emitted_after_the_outcome():
    """The spec calls context a terminator "emitted after completed/error",
    and names the reverse order as a falsifier of the design.

    This runtime therefore fails the recorded fixture for context.append,
    because that fixture was captured from the reference runtime, which emits
    context first. Tracked as hal-conformance issue #2. This test pins the
    spec-correct behaviour so a future change cannot quietly "fix" the
    fixture mismatch by adopting the bug.
    """
    events, code = run_envelope({
        "v": "happi/1.3", "id": "c", "cmd": "context.append",
        "args": ['{"decision":"d","rationale":"r"}'],
        "flags": {"kind": "context-delta", "model_versions": ["m"]},
    })
    assert code == 0
    kinds = types_of(events)
    assert "context" in kinds
    assert kinds.index("completed") < kinds.index("context")


def test_content_address_ignores_volatile_fields():
    """Two bodies identical but for id/ts/audit share one address.

    That equality IS the dedup coordinate: without it, the same belief
    recorded twice would occupy two chain nodes.
    """
    body = {"decision": "accept", "rationale": "because"}
    noisy = {**body, "id": "xyz", "ts": 999, "audit": True}
    assert content_addr(body) == content_addr(noisy)


def test_content_address_changes_when_meaning_changes():
    assert content_addr({"decision": "accept"}) != \
        content_addr({"decision": "reject"})


def test_content_address_is_key_order_independent():
    assert content_addr({"a": 1, "b": 2}) == content_addr({"b": 2, "a": 1})


def test_content_address_matches_the_cross_runtime_value():
    """Pinned against the value the reference runtime produces for the body in
    conformance fixture 07. If this drifts, context chains written by hal-py
    stop deduplicating against chains written by any other runtime — the
    failure the content address exists to prevent."""
    body = {
        "decision": ("Accept happi/1.0 through happi/1.3 rather than one "
                     "exact version"),
        "rationale": ("A runtime stamps events with its own version, never "
                      "the envelope's"),
    }
    assert content_addr(body) == (
        "sha256:84c0b0be810d70c7d1eb86e3609cbf0df9697a7a3b145e349332c8bb"
        "8133136f"
    )


def test_context_append_rejects_a_non_object_body():
    events, code = run_envelope({"v": "happi/1.3", "id": "c",
                                 "cmd": "context.append", "args": ['"a string"']})
    assert code != 0
    assert [e["code"] for e in events if e["type"] == "error"] == ["parse_error"]


# ---------------------------------------------------------------------------
# cite.verify — the provenance floor
# ---------------------------------------------------------------------------

SOURCE = "The Limitation Act 1980 bars the claim after six years."


def cite_env(strict: bool = False, quote: str = "bars the claim after six years"):
    flags = {
        "sources": [{"id": "s1", "text": SOURCE}],
        "citations": [{"id": "c1", "source_id": "s1", "quote": quote}],
    }
    if strict:
        flags["strict"] = True
    return {"v": "happi/1.3", "id": "cv", "cmd": "cite.verify", "flags": flags}


def test_a_verbatim_quote_is_verified_with_offsets_into_the_source():
    events, code = run_envelope(cite_env())
    assert code == 0
    citation = events[-1]["usage"]["citations"][0]
    assert citation["status"] == "verified"
    assert SOURCE[citation["start"]:citation["end"]] == \
        "bars the claim after six years"


def test_a_fabricated_quote_can_never_be_verified():
    """The entire guarantee. A quote that is not in the source must come back
    not_found — never verified, never fuzzy — because a caller uses this to
    decide whether a claim is grounded."""
    events, code = run_envelope(
        cite_env(quote="bars the claim after three years"))
    assert code == 0
    citation = events[-1]["usage"]["citations"][0]
    assert citation["status"] == "not_found"
    assert (citation["start"], citation["end"]) == (-1, -1)


@pytest.mark.parametrize("fabricated", [
    "bars the claim after three years",
    "permits the claim after six years",
    "The Limitation Act 1990 bars the claim",
    "entirely unrelated sentence",
])
def test_fabrications_are_never_verified_or_fuzzy(fabricated):
    events, _ = run_envelope(cite_env(quote=fabricated))
    assert events[-1]["usage"]["citations"][0]["status"] == "not_found"


def test_strict_mode_turns_an_unverified_citation_into_a_failed_build():
    events, code = run_envelope(
        cite_env(strict=True, quote="bars the claim after three years"))
    assert code != 0
    errors = [e for e in events if e["type"] == "error"]
    assert errors[0]["code"] == "runtime_error"
    assert "NOT verbatim" in errors[0]["message"]


def test_strict_mode_passes_when_everything_is_grounded():
    _, code = run_envelope(cite_env(strict=True))
    assert code == 0


def test_grounding_rate_reports_the_proportion_verified():
    events, _ = run_envelope({
        "v": "happi/1.3", "id": "cv", "cmd": "cite.verify",
        "flags": {
            "sources": [{"id": "s1", "text": SOURCE}],
            "citations": [
                {"id": "c1", "source_id": "s1",
                 "quote": "bars the claim after six years"},
                {"id": "c2", "source_id": "s1",
                 "quote": "bars the claim after three years"},
            ],
        },
    })
    usage = events[-1]["usage"]
    assert usage["tally"] == {"verified": 1, "fuzzy": 0, "not_found": 1}
    assert usage["grounding_rate"] == 0.5


def test_a_citation_naming_an_unknown_source_is_not_found():
    events, _ = run_envelope({
        "v": "happi/1.3", "id": "cv", "cmd": "cite.verify",
        "flags": {
            "sources": [{"id": "s1", "text": SOURCE}],
            "citations": [{"id": "c1", "source_id": "nope", "quote": "x"}],
        },
    })
    assert events[-1]["usage"]["citations"][0]["status"] == "not_found"


# ---------------------------------------------------------------------------
# verify_quote as a pure function
# ---------------------------------------------------------------------------

def test_whitespace_differences_match_as_fuzzy_not_verified():
    status, _, _ = verify_quote("bars   the claim", "x bars the claim y")
    assert status == "fuzzy"


def test_typographic_quotes_and_dashes_fold():
    status, start, end = verify_quote("it’s well-known",
                                      "so it's well‐known now")
    assert status == "fuzzy"
    assert start >= 0 and end > start


def test_fuzzy_offsets_index_the_original_source():
    """Typographic folding is one character to one character precisely so an
    offset found in folded text still points at the right place in the text
    the caller holds."""
    source = "so it's well‐known now"
    status, start, end = verify_quote("it’s well-known", source)
    assert status == "fuzzy"
    assert source[start:end] == "it's well‐known"


def test_an_empty_quote_is_not_found_rather_than_matching_everything():
    assert verify_quote("", "any text at all")[0] == "not_found"
    assert verify_quote("   ", "any text at all")[0] == "not_found"


# ---------------------------------------------------------------------------
# adversarial input — raised by an external review council
# ---------------------------------------------------------------------------

def test_a_quote_of_regex_metacharacters_is_matched_literally():
    """The tolerant pass builds a regex from the quote, so leaving the quote
    unescaped would let a caller inject a pattern instead of searching for
    text."""
    source = "literal (a+)+$ here"
    status, start, end = verify_quote("(a+)+$", source)
    assert status == "verified"
    assert source[start:end] == "(a+)+$"


def test_metacharacter_quote_absent_from_source_is_not_found():
    # The complement of the test above: escaping must not turn into matching
    # nothing, nor into matching everything.
    assert verify_quote(".*", "no wildcards here")[0] == "not_found"


def test_a_pathological_quote_does_not_hang():
    """Bounded work on adversarial input.

    The pattern shape is literal(\\s+literal)*, with no nested quantifiers and
    no alternation, so it cannot backtrack exponentially, and measured worst
    cases scale linearly. This pins that property rather than trusting the
    reasoning behind it.
    """
    quote = " ".join(["a"] * 400)
    source = " ".join(["a"] * 399) + " b"
    started = time.perf_counter()
    status, _, _ = verify_quote(quote, source)
    elapsed = time.perf_counter() - started
    assert status == "not_found"
    assert elapsed < 2.0, f"tolerant pass took {elapsed:.2f}s on adversarial input"


def test_an_over_long_quote_skips_fuzzing_but_still_matches_exactly():
    """Past the token limit the tolerant pass is skipped, but the exact pass
    still runs — so a genuinely present long quotation is still verified, and
    an absent one errs toward not_found rather than a false verified."""
    long_quote = " ".join(["word"] * 600)
    assert verify_quote(long_quote, "prefix " + long_quote + " suffix")[0] == "verified"
    assert verify_quote(long_quote, "nothing like it here")[0] == "not_found"


# ---------------------------------------------------------------------------
# in-process dispatch
# ---------------------------------------------------------------------------

def test_dispatch_returns_the_exit_code_without_a_subprocess(capsys):
    code = dispatch('{"v":"happi/1.3","id":"d","cmd":"version"}')
    assert code == 0
    lines = [x for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert [json.loads(x)["type"] for x in lines] == \
        ["started", "delta", "completed"]
