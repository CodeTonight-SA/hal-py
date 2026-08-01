"""Protocol version policy — which happi/N.M strings this library accepts.

Why a policy and not a list
---------------------------
A HAPPI runtime stamps every event it emits with ITS OWN version, never the
version of the envelope it was handed. So a client library will routinely meet
events carrying a version it has never heard of, simply because the runtime on
the other end of the pipe is newer than the library. That is the normal case,
not an error case.

An earlier version of this module hard-coded the exact set of minors that
existed the day it was written. Every one of those releases would have started
rejecting a perfectly conforming happi/1.4 runtime the moment one shipped — and
the failure is quiet: a decoder that returns "unsupported version" drops the
whole stream, so the caller sees no output rather than an obvious error.

The policy here is the one the spec's own compatibility rules imply: accept any
minor within the major version this library implements. A minor bump is
additive by definition (new optional event types, new optional fields), and the
decoders here ignore unknown fields, so a future minor degrades to "the parts I
understand" rather than "nothing at all". A MAJOR bump is what signals a
breaking change, and that is what this module refuses.

KNOWN_VERSIONS is retained for documentation and error messages only. Nothing
branches on it, so adding a minor to it changes no behaviour.
"""
from __future__ import annotations

#: The protocol version this library emits on envelopes it builds.
VERSION = "happi/1.3"

#: Every accepted version string starts with this.
PREFIX = "happi/"

#: The single major version this library implements.
SUPPORTED_MAJOR = 1

#: Minors that existed when this library was written. Informational only —
#: acceptance is decided by is_accepted(), which admits any happi/1.x.
KNOWN_VERSIONS = ("happi/1.0", "happi/1.1", "happi/1.2", "happi/1.3")


def _parse(value: object) -> tuple[int, int] | None:
    """Split "happi/1.3" into (1, 3). Returns None if it is not that shape."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return None
    _, _, rest = value.partition(PREFIX)
    major_text, sep, minor_text = rest.partition(".")
    if not sep:
        return None
    try:
        return int(major_text), int(minor_text)
    except ValueError:
        return None


def is_accepted(value: object) -> bool:
    """True if this library can process a stream carrying this version.

    Accepts any minor of the supported major, including minors that did not
    exist when this code was written. Rejects other majors and malformed
    strings.
    """
    parsed = _parse(value)
    if parsed is None:
        return False
    major, minor = parsed
    return major == SUPPORTED_MAJOR and minor >= 0


def accepted_list() -> str:
    """Human-readable description of the accepted range, for error messages."""
    return f"{PREFIX}{SUPPORTED_MAJOR}.x"
