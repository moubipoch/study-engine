"""Run Claude Code headless (`claude -p`) on the user's subscription and stream its events.

This is how the GUI triggers the reasoning layer (slash commands like /digest, /ask, /grade)
without an API key: it is the same Claude Code the user would run in a terminal.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

# Claude may read files, use the study CLI, write scratch files in data/tmp, and read the public course
# catalogue; anything else needs the user in a real terminal.
DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Bash(uv run study:*)", "Edit(./data/tmp/**)", "WebFetch(domain:vvz.ethz.ch)"]


@dataclass
class Event:
    kind: str  # text | tool | result | limit | error | status
    text: str = ""
    data: dict | None = None


def claude_available() -> bool:
    return shutil.which("claude") is not None


def _tool_summary(block: dict) -> str:
    inp = block.get("input") or {}
    name = block.get("name", "tool")
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url")
              or inp.get("query") or inp.get("skill") or "")
    return f"{name}: {detail}"[:200]


async def run_claude(prompt: str, cwd: Path, *, allowed_tools: list[str] | None = None,
                     session_id: str | None = None) -> AsyncIterator[Event]:
    """Yield Events while `claude -p` works. Text arrives as incremental deltas."""
    if not claude_available():
        yield Event("error", "The `claude` CLI is not installed or not on PATH.")
        return
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--allowedTools", *(allowed_tools or DEFAULT_ALLOWED_TOOLS)]
    if session_id:
        cmd += ["--resume", session_id]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
    assert proc.stdout is not None
    try:
        async for ev in _events(proc):
            yield ev
    finally:
        if proc.returncode is None:  # cancelled by the user (Stop button)
            proc.kill()
            await proc.wait()


async def _events(proc) -> AsyncIterator[Event]:
    async for raw in proc.stdout:
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "stream_event":
            ev = e.get("event", {})
            if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                yield Event("text", ev["delta"]["text"])
            elif ev.get("type") == "message_stop":
                yield Event("text", "\n\n")
        elif t == "assistant":
            for block in e.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    yield Event("tool", _tool_summary(block))
        elif t == "system" and e.get("subtype") == "init":
            yield Event("status", "started", {"session_id": e.get("session_id")})
        elif t == "rate_limit_event":
            yield Event("limit", "", e.get("rate_limit_info") or {})
        elif t == "result":
            yield Event("result", e.get("result") or "", {"session_id": e.get("session_id"),
                                                          "is_error": e.get("is_error", False),
                                                          "duration_ms": e.get("duration_ms")})
    rc = await proc.wait()
    if rc != 0:
        err = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
        yield Event("error", f"claude exited with code {rc}\n{err[-2000:]}")
