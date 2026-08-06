# hal-py

HAPPI/1.3 Python client library — **AI is a syscall**.

`happi.md` is the protocol (one envelope in, NDJSON events out, four axioms,
seven streaming event types plus two record terminators). `hal-py` is the
Python idiom for it: import once, dispatch envelopes, parse events.
Stdlib-only at runtime — no SDK pinning, no provider lock-in.

## Protocol versions

`hal-py` **emits** `happi/1.3` and **accepts** `happi/1.0` through `happi/1.3`.

A runtime always stamps events with *its own* version, never the envelope's.
So a `happi/1.0` request sent to a `happi/1.3` runtime comes back as
`happi/1.3` events — which means a client that checks for exact version
equality can only ever talk to one runtime build. Accepting the whole
supported range is what makes the client interoperable. Versions outside the
range are still rejected.

## Install

```bash
pip install hal-py
```

Requires Python 3.10+.

## Quickstart

```python
from hal_py import envelope, sync_dispatch

env = envelope.new("version", id="hello")
events = sync_dispatch(env, runtime_cmd=["bash", "/path/to/happi.md", "run"])
for e in events:
    print(e.type, getattr(e, "text", ""))
```

For an Anthropic-direct path that bypasses the runtime:

```python
from hal_py import envelope
from hal_py.providers import dispatch_anthropic

env = envelope.new(
    "anthropic.messages.create",
    id="r1",
    args=[{"messages": [{"role": "user", "content": "hello"}]}],
    auth={"token": "env:ANTHROPIC_API_KEY"},
)
for event in dispatch_anthropic(env):
    if event.type == "delta":
        print(event.text)
```

## Async

```python
from hal_py import envelope, async_dispatch

env = envelope.new("version", id="async-1")
async for event in async_dispatch(env, runtime_cmd=["bash", "happi.md", "run"]):
    print(event.type)
```

## Citation provenance (v1.3)

`cite.verify` checks that every cited quote is verbatim-present in its source.
The check is a deterministic string match, so a fabricated quote can never
earn `verified` — it resolves to `not_found`.

```python
from hal_py import cite, sync_dispatch

env = cite.new(
    sources=[cite.Source("s1", "The Act bars the claim after six years.")],
    citations=[
        cite.Citation("c1", "s1", "bars the claim after six years"),   # real
        cite.Citation("c2", "s1", "bars the claim after three years"), # invented
    ],
    id="cv-1",
)
record = cite.provenance_from_events(
    sync_dispatch(env, runtime_cmd=["bash", "happi.md", "run"]))

print(record.grounding_rate)              # 0.5
print([c.id for c in record.not_found])   # ['c2']
```

Pass `strict=True` to make the runtime exit non-zero on any unverified
citation — a build gate. Note what this proves: the quote really is in the
*supplied* source. It does **not** prove the source is authentic, nor that the
quote supports the claim.

## Audit records (v1.1)

Two non-streaming records may follow the `completed`/`error` event:

| Type | Carries | Answers |
|------|---------|---------|
| `idr` | `idr_ref` | "What exactly happened in this run?" |
| `context` | `context_ref` | "What is the identity of this belief, regardless of when it was recorded?" |

Because they arrive *after* the outcome event, the outcome is not always the
last event in the stream. Use `is_terminal()` for the outcome and
`is_post_terminator()` for these records.

## Why a syscall?

Every external boundary in a modern AI stack — Anthropic, OpenAI, Gemini,
Groq, Ollama — has its own SDK, its own auth scheme, its own retry policy,
its own streaming format. That's three layers of accidental complexity
between your code and the model.

HAPPI collapses it to one: a JSON envelope on stdin, NDJSON events on
stdout. The shim is the operating-system contract. The implementation
behind it is hot-swappable. Your code never imports a vendor SDK again.

`happi.md` is the spec. `hal-py` is the library that lets a Python program
treat any HAPPI runtime as if it were a local function call.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Live cross-runtime conformance runs against a real `happi.md`. Point at one
to enable it (the tests skip cleanly without it):

```bash
curl -fsSL "https://gist.githubusercontent.com/LaurieScheepers/2483d5c218c21ecc931130bcee7dee83/raw/happi.md" -o /tmp/happi.md
HAPPI_MD=/tmp/happi.md pytest tests/ -v
```

## Reference

- Spec: <https://gist.github.com/LaurieScheepers/2483d5c218c21ecc931130bcee7dee83>

## Licence

AGPL-3.0-only. Copyright (C) 2024-2026 Lourens Cornelius Scheepers /
CodeTonight (Pty) Ltd.

Under the AGPL, running a modified version of this library as part of a
network service counts as distribution — users interacting with it over a
network are entitled to the corresponding source. Full text in
[LICENSE](LICENSE).
