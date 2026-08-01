"""Tests for hal_py.events.

Per happi.md axiom 2 there are exactly 7 event types. Each test asserts
the parser produces the right typed subclass with the right field values.
Goodhart-resistant: tests fail under field-renaming, type-confusion, or
silent dropping of any of the 7 types.
"""
import pytest

from hal_py.events import (
    VALID_TYPES,
    CompletedEvent,
    DeltaEvent,
    ErrorEvent,
    Event,
    EventError,
    StartedEvent,
    SubRequestEvent,
    ToolCallEvent,
    ToolResultEvent,
    from_json_line,
    is_terminal,
    parse_stream,
)


# ---------------------------------------------------------------------------
# Sample NDJSON stream covering ALL 7 event types — the falsifier for axiom 2
# ---------------------------------------------------------------------------

SAMPLE_STREAM = (
    '{"v":"happi/1.0","id":"r1","type":"started","ts":0}\n'
    '{"v":"happi/1.0","id":"r1","type":"delta","ts":1,"text":"chunk one"}\n'
    '{"v":"happi/1.0","id":"r1","type":"tool_call","ts":2,'
    '"name":"calculator","input":{"x":2,"y":3},"call_id":"c1"}\n'
    '{"v":"happi/1.0","id":"r1","type":"tool_result","ts":3,'
    '"name":"calculator","output":5,"call_id":"c1"}\n'
    '{"v":"happi/1.0","id":"r1","type":"sub_request","ts":4,'
    '"envelope":{"v":"happi/1.0","id":"sub","cmd":"version"}}\n'
    '{"v":"happi/1.0","id":"r1","type":"completed","ts":5,'
    '"usage":{"input_tokens":10,"output_tokens":20}}\n'
    '{"v":"happi/1.0","id":"r2","type":"error","ts":6,'
    '"code":"runtime_error","message":"boom"}\n'
)


def test_all_seven_streaming_types_present_in_module():
    # Falsifier for axiom 2 at the API surface: the 7 streaming types must
    # all still be present. Written as a subset check so adding a v1.1
    # terminator cannot silently delete one of the original seven.
    assert {
        "started", "delta", "tool_call", "tool_result",
        "sub_request", "completed", "error",
    } <= set(VALID_TYPES)


def test_v11_terminators_present_in_module():
    # v1.1 adds two non-streaming terminators on top of the 7 streaming types.
    assert set(VALID_TYPES) == {
        "started", "delta", "tool_call", "tool_result",
        "sub_request", "completed", "error",
        "idr", "context",
    }
    assert len(VALID_TYPES) == 9


def test_parse_stream_yields_all_seven_types():
    events = list(parse_stream(SAMPLE_STREAM))
    types = [e.type for e in events]
    assert types == [
        "started", "delta", "tool_call", "tool_result",
        "sub_request", "completed", "error",
    ]


def test_started_fields():
    line = '{"v":"happi/1.0","id":"r","type":"started","ts":0}'
    event = from_json_line(line)
    assert isinstance(event, StartedEvent)
    assert event.id == "r"
    assert event.ts == 0


def test_delta_text_preserved():
    line = '{"v":"happi/1.0","id":"r","type":"delta","ts":1,"text":"hello world"}'
    event = from_json_line(line)
    assert isinstance(event, DeltaEvent)
    assert event.text == "hello world"


def test_tool_call_fields():
    line = ('{"v":"happi/1.0","id":"r","type":"tool_call","ts":2,'
            '"name":"calc","input":{"a":1},"call_id":"c1"}')
    event = from_json_line(line)
    assert isinstance(event, ToolCallEvent)
    assert event.name == "calc"
    assert event.input == {"a": 1}
    assert event.call_id == "c1"


def test_tool_result_fields():
    line = ('{"v":"happi/1.0","id":"r","type":"tool_result","ts":3,'
            '"name":"calc","output":42,"call_id":"c1"}')
    event = from_json_line(line)
    assert isinstance(event, ToolResultEvent)
    assert event.output == 42


def test_sub_request_envelope_preserved():
    line = ('{"v":"happi/1.0","id":"r","type":"sub_request","ts":4,'
            '"envelope":{"v":"happi/1.0","id":"sub","cmd":"echo"}}')
    event = from_json_line(line)
    assert isinstance(event, SubRequestEvent)
    assert event.envelope == {"v": "happi/1.0", "id": "sub", "cmd": "echo"}


def test_completed_with_usage():
    line = ('{"v":"happi/1.0","id":"r","type":"completed","ts":5,'
            '"usage":{"input_tokens":12,"output_tokens":34}}')
    event = from_json_line(line)
    assert isinstance(event, CompletedEvent)
    assert event.usage == {"input_tokens": 12, "output_tokens": 34}


def test_completed_without_usage():
    line = '{"v":"happi/1.0","id":"r","type":"completed","ts":5}'
    event = from_json_line(line)
    assert isinstance(event, CompletedEvent)
    assert event.usage is None


def test_error_fields():
    line = ('{"v":"happi/1.0","id":"r","type":"error","ts":6,'
            '"code":"auth_error","message":"missing token"}')
    event = from_json_line(line)
    assert isinstance(event, ErrorEvent)
    assert event.code == "auth_error"
    assert event.message == "missing token"


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

def test_unknown_type_raises():
    line = '{"v":"happi/1.0","id":"r","type":"surprise","ts":0}'
    with pytest.raises(EventError, match="unknown event type"):
        from_json_line(line)


def test_missing_text_in_delta_raises():
    line = '{"v":"happi/1.0","id":"r","type":"delta","ts":1}'
    with pytest.raises(EventError, match="missing field 'text'"):
        from_json_line(line)


def test_missing_code_in_error_raises():
    line = '{"v":"happi/1.0","id":"r","type":"error","ts":1,"message":"boom"}'
    with pytest.raises(EventError, match="missing field 'code'"):
        from_json_line(line)


def test_negative_ts_raises():
    line = '{"v":"happi/1.0","id":"r","type":"started","ts":-1}'
    with pytest.raises(EventError, match="invalid 'ts'"):
        from_json_line(line)


def test_wrong_version_raises():
    line = '{"v":"happi/2.0","id":"r","type":"started","ts":0}'
    with pytest.raises(EventError, match="unsupported version"):
        from_json_line(line)


def test_empty_line_raises():
    with pytest.raises(EventError, match="empty event line"):
        from_json_line("")


# ---------------------------------------------------------------------------
# Provider-specific extras round-trip via `extra` (axiom 2 sub-fields)
# ---------------------------------------------------------------------------

def test_provider_specific_extras_preserved():
    line = ('{"v":"happi/1.0","id":"r","type":"completed","ts":5,'
            '"usage":{"input_tokens":10},"provider":{"model":"claude"}}')
    event = from_json_line(line)
    assert isinstance(event, CompletedEvent)
    assert event.extra.get("provider") == {"model": "claude"}


# ---------------------------------------------------------------------------
# Terminal detection
# ---------------------------------------------------------------------------

def test_completed_is_terminal():
    e = from_json_line('{"v":"happi/1.0","id":"r","type":"completed","ts":1}')
    assert is_terminal(e) is True


def test_error_is_terminal():
    e = from_json_line('{"v":"happi/1.0","id":"r","type":"error","ts":1,'
                       '"code":"runtime_error","message":"x"}')
    assert is_terminal(e) is True


def test_started_is_not_terminal():
    e = from_json_line('{"v":"happi/1.0","id":"r","type":"started","ts":0}')
    assert is_terminal(e) is False


def test_delta_is_not_terminal():
    e = from_json_line('{"v":"happi/1.0","id":"r","type":"delta","ts":1,'
                       '"text":"x"}')
    assert is_terminal(e) is False


# ---------------------------------------------------------------------------
# Event base class invariants
# ---------------------------------------------------------------------------

def test_every_typed_event_is_an_event():
    """All 7 typed subclasses inherit from Event (single hierarchy)."""
    classes = [StartedEvent, DeltaEvent, ToolCallEvent, ToolResultEvent,
               SubRequestEvent, CompletedEvent, ErrorEvent]
    for cls in classes:
        assert issubclass(cls, Event), f"{cls.__name__} must extend Event"
