"""Optional LLM backend interface.

The default reasoning layer is Claude Code itself, driven by `.claude/commands/*`, so nothing in the
CLI calls an LLM. This interface exists so a future headless `study batch` could plug in a local
model (Ollama) or an API key without touching the rest of the code. Only ManualBackend is used today.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol


class Backend(Protocol):
    name: str

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096) -> str: ...


class BackendUnavailable(RuntimeError):
    pass


class ManualBackend:
    """Default: the reasoning happens in Claude Code via slash commands."""

    name = "manual"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096) -> str:
        raise BackendUnavailable(
            "No LLM backend configured: run the matching slash command in Claude Code "
            "(e.g. /digest, /ask, /grade) instead."
        )


class OllamaBackend:
    """Untested stub for a local model served by Ollama (http://localhost:11434)."""

    name = "ollama"

    def __init__(self, model: str = "qwen2.5:14b", url: str = "http://localhost:11434"):
        self.model, self.url = model, url

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096) -> str:
        body = {"model": self.model, "prompt": prompt, "system": system, "stream": False,
                "options": {"num_predict": max_tokens}}
        req = urllib.request.Request(f"{self.url}/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())["response"]
        except OSError as e:
            raise BackendUnavailable(f"Ollama not reachable at {self.url}: {e}") from e


class AnthropicBackend:
    """Untested stub; requires `pip install anthropic` and ANTHROPIC_API_KEY. Not a dependency."""

    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-5"):
        self.model = model

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096) -> str:
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise BackendUnavailable("anthropic package not installed") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise BackendUnavailable("ANTHROPIC_API_KEY not set")
        msg = anthropic.Anthropic().messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")


def get_backend(name: str | None = None) -> Backend:
    name = name or os.environ.get("STUDY_BACKEND", "manual")
    return {"manual": ManualBackend, "ollama": OllamaBackend, "anthropic": AnthropicBackend}[name]()
