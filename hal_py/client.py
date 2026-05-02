"""HAPPI/1.0 client — sync + async dispatch via subprocess.

Per the four axioms (happi.md): CLI (stdio) is the canonical transport.
A HAPPI client writes one JSON envelope to stdin and reads NDJSON events
from stdout. This module is the syscall surface: one API, two flavours.

    sync_dispatch(envelope, runtime_cmd) -> list[Event]
    async_dispatch(envelope, runtime_cmd) -> AsyncIterator[Event]

`runtime_cmd` is the command list used to spawn the runtime, e.g.
`["bash", "/Users/x/.hal/happi.md", "run"]`. The default points at a
locally-installed `happi` symlink (created via `bash happi.md install`).
"""
from __future__ import annotations

import asyncio
import subprocess
from typing import AsyncIterator, Iterable, Iterator, Sequence

from .envelope import Envelope
from .events import Event, EventError, from_json_line

DEFAULT_RUNTIME: tuple[str, ...] = ("happi", "run")
"""Default runtime command. Override per-call to use a different runtime."""


class DispatchError(RuntimeError):
    """Raised when the runtime exits non-zero AND emits no error event."""


def _resolve_cmd(runtime_cmd: Sequence[str] | None) -> list[str]:
    """Return the list form of the runtime command."""
    if runtime_cmd is None:
        return list(DEFAULT_RUNTIME)
    return list(runtime_cmd)


def sync_dispatch(envelope: Envelope,
                  runtime_cmd: Sequence[str] | None = None,
                  timeout: float | None = None) -> list[Event]:
    """Dispatch one envelope synchronously and return all events.

    The runtime is spawned as a subprocess, the envelope is written to its
    stdin, stdout is parsed as NDJSON, stderr is forwarded for diagnostics.
    """
    cmd = _resolve_cmd(runtime_cmd)
    completed = subprocess.run(
        cmd,
        input=envelope.to_json() + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    events = list(_parse_lines(completed.stdout.splitlines()))
    _check_terminal(events, completed.returncode, completed.stderr)
    return events


def _parse_lines(lines: Iterable[str]) -> Iterator[Event]:
    """Parse NDJSON lines, skipping blanks. Re-raise on protocol error."""
    for line in lines:
        if line.strip():
            yield from_json_line(line)


def _check_terminal(events: list[Event], returncode: int, stderr: str) -> None:
    """Confirm the stream ends with completed or error; raise otherwise."""
    if not events:
        raise DispatchError(
            f"runtime produced no events (exit={returncode}, stderr={stderr!r})"
        )
    last = events[-1]
    if last.type not in ("completed", "error"):
        raise DispatchError(
            f"runtime stream did not terminate: last={last.type!r} "
            f"(exit={returncode})"
        )


async def async_dispatch(envelope: Envelope,
                         runtime_cmd: Sequence[str] | None = None
                         ) -> AsyncIterator[Event]:
    """Dispatch asynchronously. Yields events as they arrive on stdout.

    Returns an async iterator; consume with `async for`. The subprocess is
    awaited to completion after the stream ends; if the runtime exits
    non-zero without emitting a terminal event, DispatchError is raised.
    """
    cmd = _resolve_cmd(runtime_cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if proc.stdin is None or proc.stdout is None:
        raise DispatchError("runtime subprocess pipes unavailable")
    proc.stdin.write((envelope.to_json() + "\n").encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    saw_terminal = False
    async for event in _aiter_events(proc.stdout):
        if event.type in ("completed", "error"):
            saw_terminal = True
        yield event

    returncode = await proc.wait()
    stderr_bytes = await proc.stderr.read() if proc.stderr else b""
    if not saw_terminal:
        raise DispatchError(
            f"runtime stream did not terminate: exit={returncode}, "
            f"stderr={stderr_bytes!r}"
        )


async def _aiter_events(stream: asyncio.StreamReader) -> AsyncIterator[Event]:
    """Async-iterate NDJSON events off a stream reader."""
    while True:
        raw = await stream.readline()
        if not raw:
            return
        text = raw.decode("utf-8").strip()
        if not text:
            continue
        try:
            yield from_json_line(text)
        except EventError:
            # Skip unparseable lines; runtime may emit non-event diagnostics.
            continue
