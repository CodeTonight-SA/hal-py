"""HAPPI/1.0 provider adapters.

Provider adapters map HAPPI envelopes to provider-specific HTTP calls and
back. v0.1 ships only the Anthropic adapter; v0.2+ will add OpenAI,
Gemini, Groq, Ollama, etc. Each adapter is a leaf module that the
`client.dispatch` syscall surface treats opaquely — the protocol is the
interface, not the SDK.
"""
from .anthropic import dispatch_anthropic

__all__ = ["dispatch_anthropic"]
