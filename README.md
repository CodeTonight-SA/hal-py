# hal-py

HAPPI/1.0 Python client library — **AI is a syscall**.

`happi.md` is the protocol (one envelope in, NDJSON events out, four axioms,
seven event types). `hal-py` is the Python idiom for it: import once,
dispatch envelopes, parse events. Stdlib-only at runtime — no SDK pinning,
no provider lock-in.

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

## Why a syscall?

Every external boundary in a modern AI stack — Anthropic, OpenAI, Gemini,
Groq, Ollama — has its own SDK, its own auth scheme, its own retry policy,
its own streaming format. That's three layers of accidental complexity
between your code and the model.

HAPPI/1.0 collapses it to one: a JSON envelope on stdin, NDJSON events on
stdout. The shim is the operating-system contract. The implementation
behind it is hot-swappable. Your code never imports a vendor SDK again.

`happi.md` is the spec. `hal-py` is the library that lets a Python program
treat any HAPPI runtime as if it were a local function call.

## Reference

- Spec: <https://github.com/CodeTonight-SA/HAL/blob/main/happi.md>
- License: MIT
