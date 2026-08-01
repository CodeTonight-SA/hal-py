"""A HAPPI/1.3 runtime, so hal-py can be conformance-tested as a runtime.

Until now hal-py was a client library only: it could speak TO a runtime but
could not BE one, which meant the cross-runtime conformance suite had nothing
to point at. `python3 -m hal_py.run` reads one envelope on stdin and writes an
NDJSON event stream on stdout, which is the whole protocol contract.

Run it directly:

    echo '{"v":"happi/1.3","id":"1","cmd":"echo","args":["hi"]}' | python3 -m hal_py.run

One deliberate divergence from the reference runtime
----------------------------------------------------
For `context.append` this runtime emits the `context` event AFTER the outcome
event. The reference emits it BEFORE. The spec is explicit that after is
correct — HAPPI.md calls a context event "a non-streaming terminator emitted
after completed/error" and lists "the context event arrives before
completed/error" as a named falsifier of the design.

The consequence is worth stating plainly: this runtime FAILS the recorded
fixture for context.append, because that fixture was recorded from the
reference and therefore records the reference's ordering. A conformance suite
built only from recordings cannot tell "this implementation is wrong" apart
from "this implementation disagrees with a bug", and following the recording
here would mean writing a known spec violation into a second implementation.
Tracked as hal-conformance issue #2; when the reference is fixed and the
fixture re-recorded, this runtime will match it with no change.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from typing import Any, Callable

from . import envelope as envelope_mod
from . import happi

#: Process start, so ts is a small monotonic offset rather than a wall clock.
_START_NS = time.time_ns()

#: Buffer of emitted event lines, hashed into an idr when flags.audit is set.
_AUDIT: dict[str, Any] = {"enabled": False, "model_versions": [], "lines": []}

#: Volatile fields excluded from a context content address, so two records
#: with identical meaning but different id/ts share one address.
_CONTENT_ADDR_EXCLUDE = ("id", "ts", "audit")


# --- emitters ---------------------------------------------------------------

def _ts_ms() -> int:
    return max(0, (time.time_ns() - _START_NS) // 1_000_000)


def _emit(event: dict[str, Any]) -> None:
    line = json.dumps(event, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    if _AUDIT["enabled"]:
        _AUDIT["lines"].append(line)


def _event(req_id: str, etype: str, **extra: Any) -> dict[str, Any]:
    return {"v": happi.VERSION, "id": req_id, "type": etype,
            "ts": _ts_ms(), **extra}


def emit_started(req_id: str) -> None:
    _emit(_event(req_id, "started"))


def emit_delta(req_id: str, text: str) -> None:
    _emit(_event(req_id, "delta", text=text))


def emit_completed(req_id: str, usage: dict[str, Any] | None = None) -> None:
    event = _event(req_id, "completed")
    if usage is not None:
        event["usage"] = usage
    _emit(event)


def emit_error(req_id: str, code: str, message: str) -> None:
    _emit(_event(req_id, "error", code=code, message=message))


def content_addr(body: dict[str, Any]) -> str:
    """Content address of a decision body: sha256 over canonical JSON with
    sorted keys, excluding the volatile id/ts/audit fields.

    Byte-identical to the reference runtime and to GRIP's
    lib/precog/idr.py::content_addr for the same body. That equality is the
    whole point — it is what lets a context chain written by one runtime be
    deduplicated and merkle-linked by another.
    """
    addressed = {k: v for k, v in body.items()
                 if k not in _CONTENT_ADDR_EXCLUDE}
    canonical = json.dumps(addressed, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def emit_context(req_id: str, body: dict[str, Any],
                 meta: dict[str, Any]) -> None:
    _emit(_event(req_id, "context", context_ref={
        "sha256": content_addr(body),
        "predecessor_context": meta.get("predecessor_context"),
        "snapshot_ref": meta.get("snapshot_ref"),
        "kind": meta.get("kind") or "context-delta",
        "model_versions": list(meta.get("model_versions") or []),
    }))


def emit_idr(req_id: str, envelope_raw: str) -> None:
    """Emit the audit receipt: sha256 over the envelope bytes followed by each
    emitted event line, so the same (envelope, stream) always hashes the same.
    """
    digest = hashlib.sha256()
    digest.update(envelope_raw.encode("utf-8"))
    for line in _AUDIT["lines"]:
        digest.update(line.encode("utf-8"))
    _emit(_event(req_id, "idr", idr_ref={
        "sha256": digest.hexdigest(),
        "cid": None,
        "model_versions": list(_AUDIT["model_versions"]),
        "block_anchor": None,
    }))


# --- citation verification --------------------------------------------------

_WS = re.compile(r"\s+")

# Length-preserving typographic folding: each mapping is one character to one
# character, so an offset found in folded text still indexes the ORIGINAL
# source. A folding that changed length would report offsets into a string the
# caller never sees.
_TYPO = {
    0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-",
    0x2015: "-",
    0x2018: "'", 0x2019: "'", 0x201B: "'",
    0x201C: '"', 0x201D: '"', 0x201F: '"',
    0x00A0: " ", 0x2007: " ", 0x2009: " ", 0x202F: " ",
}


def _fold(text: str) -> str:
    return text.translate(_TYPO)


def verify_quote(quote: str, source_text: str) -> tuple[str, int, int]:
    """Locate `quote` in `source_text`. Returns (status, start, end).

    A deterministic ladder, never a model call: exact substring, then a
    whitespace- and typography-flexible match, then not_found. Determinism is
    the guarantee — a fabricated quote cannot come back verified because there
    is no judgement anywhere in the path, only string search.
    """
    needle = (quote or "").strip()
    if not needle:
        return "not_found", -1, -1
    at = source_text.find(needle)
    if at != -1:
        return "verified", at, at + len(needle)
    tokens = [re.escape(t) for t in _WS.split(_fold(needle)) if t]
    if not tokens:
        return "not_found", -1, -1
    match = re.compile(r"\s+".join(tokens)).search(_fold(source_text))
    if match:
        return "fuzzy", match.start(), match.end()
    return "not_found", -1, -1


def _index_sources(sources: list[Any]) -> tuple[dict[str, str] | None, str | None]:
    by_id: dict[str, str] = {}
    for source in sources:
        if (not isinstance(source, dict)
                or not isinstance(source.get("id"), str)
                or not isinstance(source.get("text"), str)):
            return None, "cite.verify: each source needs string id and text"
        by_id[source["id"]] = source["text"]
    return by_id, None


def _citation_ok(citation: Any) -> bool:
    return (isinstance(citation, dict)
            and isinstance(citation.get("id"), str)
            and isinstance(citation.get("source_id"), str)
            and isinstance(citation.get("quote"), str))


def _process_citations(
    req_id: str, citations: list[Any], by_id: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, int]] | None:
    tally = {"verified": 0, "fuzzy": 0, "not_found": 0}
    results: list[dict[str, Any]] = []
    for citation in citations:
        if not _citation_ok(citation):
            emit_error(req_id, "parse_error",
                       "cite.verify: each citation needs string id, "
                       "source_id, quote")
            return None
        source_text = by_id.get(citation["source_id"])
        if source_text is None:
            status, start, end = "not_found", -1, -1
        else:
            status, start, end = verify_quote(citation["quote"], source_text)
        tally[status] += 1
        results.append({"id": citation["id"],
                        "source_id": citation["source_id"],
                        "status": status, "start": start, "end": end})
        emit_delta(req_id, citation["id"] + " " + status)
    return results, tally


# --- commands ---------------------------------------------------------------

_SPEC_LINES = (
    "HAPPI/1.3 - Harnessed-AI Polyglot Protocol Interface",
    "One JSON envelope in (stdin), one NDJSON event stream out (stdout).",
    "Envelope: {v, id, cmd, args?, flags?, auth?}",
    "Events: started -> (delta|tool_call|tool_result|sub_request)* -> "
    "completed|error -> [idr]",
    "v1.1: flags.audit=true emits idr terminator with sha256 of "
    "envelope+events",
    "v1.1: cmd context.append emits a context terminator content-addressing "
    "a decision body (excl id/ts/audit)",
    "v1.3: cmd cite.verify deterministically checks each citation quote is "
    "verbatim in its source (un-fakeable provenance floor)",
    "Cmds (this runtime): version, echo, spec.describe, envelope.validate, "
    "idr.emit, context.append, cite.verify",
    "Implementation: hal-py  Reference: happi.md",
)


def cmd_version(env: dict[str, Any]) -> int:
    """Report the version of THIS runtime, never the envelope's.

    A runtime that echoed the envelope's version back would make every
    version negotiation a no-op and hide a mismatch instead of exposing it.
    """
    emit_delta(env["id"], happi.VERSION)
    emit_completed(env["id"])
    return 0


def cmd_echo(env: dict[str, Any]) -> int:
    args = env.get("args") or []
    if not isinstance(args, list):
        emit_error(env["id"], "parse_error", "echo: 'args' must be an array")
        return 1
    for arg in args:
        emit_delta(env["id"], str(arg))
    emit_completed(env["id"])
    return 0


def cmd_spec_describe(env: dict[str, Any]) -> int:
    for line in _SPEC_LINES:
        emit_delta(env["id"], line)
    emit_completed(env["id"])
    return 0


def cmd_envelope_validate(env: dict[str, Any]) -> int:
    """Reaching this point already means the envelope validated, because
    dispatch refuses malformed envelopes before command lookup."""
    emit_delta(env["id"], "envelope OK: passed schema validation")
    emit_completed(env["id"], {"validated": True})
    return 0


def cmd_idr_emit(env: dict[str, Any]) -> int:
    emit_completed(env["id"])
    return 0


def cmd_context_append(env: dict[str, Any]) -> int:
    """Content-address a decision body and emit a context event.

    Emission order is outcome first, then context — see the module docstring
    for why this deliberately diverges from the reference runtime.
    """
    args = env.get("args") or []
    if not args:
        emit_error(env["id"], "parse_error",
                   "context.append: requires args[0] = decision body JSON")
        return 1
    raw_body = args[0]
    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except json.JSONDecodeError as exc:
        emit_error(env["id"], "parse_error",
                   f"context.append: args[0] is not valid JSON: {exc}")
        return 1
    if not isinstance(body, dict):
        emit_error(env["id"], "parse_error",
                   "context.append: decision body must be a JSON object")
        return 1
    emit_completed(env["id"], {"content_addr": content_addr(body)})
    emit_context(env["id"], body, env.get("flags") or {})
    return 0


def cmd_cite_verify(env: dict[str, Any]) -> int:
    """Check every citation quote is verbatim in its cited source.

    With flags.strict, any unverified citation becomes an error and a non-zero
    exit, so a build can be failed on unproven claims.
    """
    flags = env.get("flags") or {}
    sources = flags.get("sources")
    citations = flags.get("citations")
    if not isinstance(sources, list) or not isinstance(citations, list):
        emit_error(env["id"], "parse_error",
                   "cite.verify: requires flags.sources[] and flags.citations[]")
        return 1
    by_id, err = _index_sources(sources)
    if err is not None or by_id is None:
        emit_error(env["id"], "parse_error", err or "cite.verify: bad sources")
        return 1
    processed = _process_citations(env["id"], citations, by_id)
    if processed is None:
        return 1
    results, tally = processed
    grounded = tally["verified"] + tally["fuzzy"]
    record = {
        "sources": {
            sid: {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "chars": len(text)}
            for sid, text in by_id.items()
        },
        "citations": results,
        "tally": tally,
        "grounding_rate": round(grounded / max(len(citations), 1), 3),
    }
    if bool(flags.get("strict", False)) and tally["not_found"] > 0:
        emit_error(env["id"], "runtime_error",
                   f"{tally['not_found']} citation(s) NOT verbatim in "
                   "source — unproven")
        return 1
    emit_completed(env["id"], record)
    return 0


#: Command lookup. A dict keeps dispatch O(1) and flat, so adding a command
#: never lengthens a branch chain.
CMDS: dict[str, Callable[[dict[str, Any]], int]] = {
    "version": cmd_version,
    "echo": cmd_echo,
    "spec.describe": cmd_spec_describe,
    "envelope.validate": cmd_envelope_validate,
    "idr.emit": cmd_idr_emit,
    "context.append": cmd_context_append,
    "cite.verify": cmd_cite_verify,
}


# --- dispatch ---------------------------------------------------------------

def _parse_envelope(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate, reusing the library's own envelope rules rather
    than restating them, so the runtime and the client cannot drift apart."""
    if not raw.strip():
        return None, "empty stdin"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "envelope must be a JSON object"
    try:
        envelope_mod.from_dict(data)
    except envelope_mod.EnvelopeError as exc:
        return None, str(exc)
    return data, None


def _arm_audit(env: dict[str, Any]) -> None:
    flags = env.get("flags") or {}
    if flags.get("audit"):
        _AUDIT["enabled"] = True
        _AUDIT["model_versions"] = list(flags.get("model_versions") or [])


def dispatch(raw: str) -> int:
    """Run one envelope. Returns the process exit code."""
    env, err = _parse_envelope(raw)
    if env is None:
        # No id is available on a malformed envelope, so the error carries the
        # empty id rather than inventing one.
        emit_error("", "parse_error", err or "unparseable envelope")
        return 1
    _arm_audit(env)
    emit_started(env["id"])
    handler = CMDS.get(env["cmd"])
    if handler is None:
        emit_error(env["id"], "unsupported_cmd",
                   f"cmd not implemented: {env['cmd']}")
        code = 1
    else:
        code = handler(env)
    if _AUDIT["enabled"]:
        emit_idr(env["id"], raw)
    return code


def main() -> int:
    return dispatch(sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(main())
