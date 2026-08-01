"""HAPPI/1.3 cite.verify — deterministic citation provenance.

The un-fakeable floor of grounded AI, lifted to protocol level. A cited quote
is verbatim-present in its source, or it is not — a deterministic string match,
not a judgement. A fabricated quote can never earn `verified` or `fuzzy`; it
resolves to `not_found`.

This module is the typed Python surface over the `cite.verify` cmd: build the
envelope, parse the provenance record the runtime returns in `completed.usage`.
The verification itself is performed by the runtime, never here — that is the
point. A client that graded its own citations would be marking its own homework.

    from hal_py import cite, sync_dispatch

    env = cite.new(
        sources=[cite.Source("s1", "The Act bars the claim after six years.")],
        citations=[cite.Citation("c1", "s1", "bars the claim after six years")],
        id="cv-1",
    )
    events = sync_dispatch(env, runtime_cmd=["bash", "happi.md", "run"])
    record = cite.provenance_from_events(events)
    print(record.grounding_rate, record.not_found)

What it proves and what it does not: the quote is verbatim in the *supplied*
source. NOT that the source is authentic, and NOT that the quote supports the
claim. Those remain the caller's responsibility (or a higher LLM layer's).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import envelope as _envelope
from .envelope import Envelope

#: The runtime cmd this module targets.
CITE_VERIFY_CMD = "cite.verify"

#: Quote found by exact substring match.
STATUS_VERIFIED = "verified"

#: Quote found after length-preserving normalisation of whitespace and
#: typographic variants (unicode dashes, smart quotes, nbsp).
STATUS_FUZZY = "fuzzy"

#: Quote absent from its source. A fabricated citation always lands here.
STATUS_NOT_FOUND = "not_found"

#: Statuses that count as grounded.
GROUNDED_STATUSES = (STATUS_VERIFIED, STATUS_FUZZY)

VALID_STATUSES = (STATUS_VERIFIED, STATUS_FUZZY, STATUS_NOT_FOUND)


class CiteError(ValueError):
    """Raised when cite.verify inputs or the provenance record are malformed."""


@dataclass(frozen=True)
class Source:
    """A document a citation may quote."""

    id: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text}


@dataclass(frozen=True)
class Citation:
    """A claim's exact quoted words and which source they come from."""

    id: str
    source_id: str
    quote: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source_id": self.source_id, "quote": self.quote}


@dataclass(frozen=True)
class CitationResult:
    """Per-citation verdict. `start`/`end` are -1 when not found."""

    id: str
    source_id: str
    status: str
    start: int = -1
    end: int = -1

    @property
    def grounded(self) -> bool:
        """True if the quote was located (exact or normalised)."""
        return self.status in GROUNDED_STATUSES


@dataclass
class Provenance:
    """The provenance record returned in `completed.usage`.

    Same shape as GRIP `lib/prove_it.py::provenance`, so the two engines
    interoperate. The per-source sha256 plus per-citation offsets are exactly
    what an IDR or memory-chain leaf needs to sign.
    """

    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    citations: list[CitationResult] = field(default_factory=list)
    tally: dict[str, int] = field(default_factory=dict)
    grounding_rate: float = 0.0

    @property
    def verified(self) -> list[CitationResult]:
        """Citations located by exact match."""
        return [c for c in self.citations if c.status == STATUS_VERIFIED]

    @property
    def fuzzy(self) -> list[CitationResult]:
        """Citations located only after normalisation."""
        return [c for c in self.citations if c.status == STATUS_FUZZY]

    @property
    def not_found(self) -> list[CitationResult]:
        """Citations absent from their source — the fabricated ones."""
        return [c for c in self.citations if c.status == STATUS_NOT_FOUND]

    @property
    def is_fully_grounded(self) -> bool:
        """True if every citation was located in its cited source.

        False for an empty record: nothing was proven, so nothing is grounded.
        """
        return bool(self.citations) and not self.not_found


def new(sources: Sequence[Source | dict[str, Any]],
        citations: Sequence[Citation | dict[str, Any]],
        *,
        id: str,  # noqa: A002 — mirrors envelope.new's keyword
        strict: bool = False,
        auth: dict[str, Any] | None = None) -> Envelope:
    """Build a `cite.verify` envelope.

    With `strict=True` the runtime emits `error` (and exits non-zero) if ANY
    citation is not found — the build-gate form. Otherwise it emits
    `completed` and the caller reads `grounding_rate`.
    """
    if not sources:
        raise CiteError("cite.verify requires at least one source")
    if not citations:
        raise CiteError("cite.verify requires at least one citation")

    flags: dict[str, Any] = {
        "sources": [_as_dict(s) for s in sources],
        "citations": [_as_dict(c) for c in citations],
    }
    if strict:
        flags["strict"] = True
    return _envelope.new(CITE_VERIFY_CMD, id=id, flags=flags, auth=auth)


def _as_dict(item: Any) -> dict[str, Any]:
    """Accept either a typed Source/Citation or a plain dict."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    raise CiteError(f"expected Source, Citation or dict, got {type(item).__name__}")


def provenance_from_usage(usage: dict[str, Any] | None) -> Provenance:
    """Parse the provenance record out of a `completed` event's usage field."""
    if not isinstance(usage, dict):
        raise CiteError("cite.verify completed event carried no usage record")
    raw_citations = usage.get("citations", [])
    if not isinstance(raw_citations, list):
        raise CiteError("provenance 'citations' must be a list")
    return Provenance(
        sources=dict(usage.get("sources", {})),
        citations=[_result_from_dict(c) for c in raw_citations],
        tally=dict(usage.get("tally", {})),
        grounding_rate=float(usage.get("grounding_rate", 0.0)),
    )


def _result_from_dict(data: dict[str, Any]) -> CitationResult:
    """Build one CitationResult, rejecting unknown statuses."""
    if not isinstance(data, dict):
        raise CiteError("each provenance citation must be an object")
    status = data.get("status")
    if status not in VALID_STATUSES:
        raise CiteError(f"unknown citation status: {status!r}")
    return CitationResult(
        id=str(data.get("id", "")),
        source_id=str(data.get("source_id", "")),
        status=status,
        start=int(data.get("start", -1)),
        end=int(data.get("end", -1)),
    )


def provenance_from_events(events: Iterable[Any]) -> Provenance:
    """Extract the provenance record from a dispatched event stream.

    Raises CiteError if the stream carried no `completed` event — including
    the strict-mode case, where the runtime emits `error` instead.
    """
    for event in events:
        if getattr(event, "type", None) == "completed":
            return provenance_from_usage(getattr(event, "usage", None))
    raise CiteError(
        "no completed event in stream "
        "(strict mode emits error when a citation is not found)"
    )
