"""Tests for hal_py.envelope.

Goodhart-resistant per GRIP Rule 14: every test asserts actual envelope
values, not call counts or implementation details. A test that always
passes is worse than no test — these will fail under mutation.
"""
import json

import pytest

from hal_py.envelope import (
    HAPPI_VERSION,
    Envelope,
    EnvelopeError,
    from_dict,
    from_json,
    new,
)


# ---------------------------------------------------------------------------
# 10 round-trip cases (the falsifier: must encode -> decode -> equal)
# ---------------------------------------------------------------------------

ROUND_TRIP_CASES = [
    # 1. minimal
    {"v": "happi/1.0", "id": "r1", "cmd": "version"},
    # 2. with args
    {"v": "happi/1.0", "id": "r2", "cmd": "echo", "args": ["hello", "world"]},
    # 3. with flags
    {"v": "happi/1.0", "id": "r3", "cmd": "spec.describe",
     "flags": {"verbose": True, "limit": 5}},
    # 4. with auth (env: form)
    {"v": "happi/1.0", "id": "r4", "cmd": "anthropic.messages.create",
     "auth": {"scheme": "apikey", "token": "env:SOME_VAR"}},
    # 5. unknown top-level field — must round-trip per additionalProperties: true
    {"v": "happi/1.0", "id": "r5", "cmd": "version",
     "session_id": "sess-xyz"},
    # 6. nested args + flags + auth + extras
    {"v": "happi/1.0", "id": "r6", "cmd": "anthropic.messages.create",
     "args": [{"model": "claude-3-5-sonnet-latest",
               "messages": [{"role": "user", "content": "hi"}]}],
     "flags": {"max_tokens": 256, "provider_specific": {"caching": True}},
     "auth": {"token": "env:X"},
     "trace_id": "abc-123"},
    # 7. empty args/flags/auth
    {"v": "happi/1.0", "id": "r7", "cmd": "echo",
     "args": [], "flags": {}, "auth": {}},
    # 8. id with non-ASCII
    {"v": "happi/1.0", "id": "ünïcödé-id-é", "cmd": "version"},
    # 9. cmd with multiple dots
    {"v": "happi/1.0", "id": "r9",
     "cmd": "deeply.nested.cmd.path"},
    # 10. unicode in args
    {"v": "happi/1.0", "id": "r10", "cmd": "echo",
     "args": ["こんにちは", "🦊", "fração"]},
]


@pytest.mark.parametrize("case", ROUND_TRIP_CASES)
def test_dict_round_trip(case):
    env = from_dict(case)
    redict = env.to_dict()
    # Verify required fields preserved exactly.
    assert redict["v"] == case["v"]
    assert redict["id"] == case["id"]
    assert redict["cmd"] == case["cmd"]
    # Verify optional fields preserved when present.
    for fld in ("args", "flags", "auth"):
        if fld in case and case[fld]:
            assert redict[fld] == case[fld], f"{fld} mismatch in case {case['id']}"
    # Verify unknown fields round-trip via extras.
    for k in case:
        if k not in ("v", "id", "cmd", "args", "flags", "auth"):
            assert redict[k] == case[k], (
                f"unknown field {k!r} did not round-trip in case {case['id']}"
            )


@pytest.mark.parametrize("case", ROUND_TRIP_CASES)
def test_json_round_trip(case):
    raw = json.dumps(case)
    env = from_json(raw)
    redict = env.to_dict()
    # JSON re-parse should match original dict (modulo absent-empty fields).
    assert redict["v"] == case["v"]
    assert redict["id"] == case["id"]
    assert redict["cmd"] == case["cmd"]


# ---------------------------------------------------------------------------
# Validation failures (mutation discipline: changing required-check logic
# in the source must break these tests)
# ---------------------------------------------------------------------------

def test_missing_v_raises():
    with pytest.raises(EnvelopeError, match="unsupported version"):
        from_dict({"id": "x", "cmd": "version"})


def test_wrong_v_raises():
    with pytest.raises(EnvelopeError, match="unsupported version"):
        from_dict({"v": "happi/2.0", "id": "x", "cmd": "version"})


def test_missing_id_raises():
    with pytest.raises(EnvelopeError, match="missing or invalid 'id'"):
        from_dict({"v": "happi/1.0", "cmd": "version"})


def test_empty_id_raises():
    with pytest.raises(EnvelopeError, match="missing or invalid 'id'"):
        from_dict({"v": "happi/1.0", "id": "", "cmd": "version"})


def test_missing_cmd_raises():
    with pytest.raises(EnvelopeError, match="missing or invalid 'cmd'"):
        from_dict({"v": "happi/1.0", "id": "x"})


def test_args_must_be_list():
    with pytest.raises(EnvelopeError, match="'args' must be an array"):
        from_dict({"v": "happi/1.0", "id": "x", "cmd": "echo", "args": "bad"})


def test_flags_must_be_object():
    with pytest.raises(EnvelopeError, match="'flags' must be an object"):
        from_dict({"v": "happi/1.0", "id": "x", "cmd": "echo", "flags": []})


def test_empty_json_raises():
    with pytest.raises(EnvelopeError, match="empty input"):
        from_json("")


def test_invalid_json_raises():
    with pytest.raises(EnvelopeError, match="invalid JSON"):
        from_json("{not json")


def test_non_object_envelope_raises():
    with pytest.raises(EnvelopeError, match="JSON object"):
        from_dict(["not", "an", "object"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Construction conveniences
# ---------------------------------------------------------------------------

def test_new_minimal():
    env = new("version", id="hello")
    assert env.cmd == "version"
    assert env.id == "hello"
    assert env.v == HAPPI_VERSION
    assert env.args == []
    assert env.flags == {}


def test_new_with_args_flags_auth():
    env = new("echo", id="r", args=["a", "b"],
              flags={"verbose": True}, auth={"token": "env:X"})
    assert env.args == ["a", "b"]
    assert env.flags == {"verbose": True}
    assert env.auth == {"token": "env:X"}


def test_to_json_is_compact_and_parseable():
    env = new("version", id="r")
    out = env.to_json()
    # No spaces (compact form) per happi.md NDJSON convention.
    assert " " not in out
    # Round-trips cleanly through json.loads.
    parsed = json.loads(out)
    assert parsed == {"v": "happi/1.3", "id": "r", "cmd": "version"}


def test_unknown_field_round_trips_through_to_json():
    env = from_dict({"v": "happi/1.0", "id": "r", "cmd": "version",
                     "session_id": "sess-1"})
    out = env.to_json()
    reparsed = json.loads(out)
    assert reparsed["session_id"] == "sess-1"
