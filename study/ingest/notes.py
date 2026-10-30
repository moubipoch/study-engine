"""Notes and plain text: markdown split by heading (line locations); subtitle files as transcripts."""

from __future__ import annotations

import re
from pathlib import Path

from study.ingest.base import Chunk, Extracted, Image, windows_by_lines

TEXT_EXT = {".md", ".markdown", ".txt", ".tex", ".rst"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".gif"}
SUBTITLE_EXT = {".srt", ".vtt"}
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$|^\\(?:sub)*section\*?\{(.*)\}")
MAX_LINES = 80


def extract_text(path: Path) -> Extracted:
    lines = path.read_text(errors="replace").splitlines()
    sections: list[tuple[int, str]] = [(1, "")]
    for i, ln in enumerate(lines, start=1):
        if m := _HEADING.match(ln):
            sections.append((i, (m.group(2) or m.group(3) or "").strip()))
    chunks = []
    bounds = sections + [(len(lines) + 1, "")]
    for (start, title), (nxt, _) in zip(bounds, bounds[1:]):
        end = nxt - 1
        if end < start or not "".join(lines[start - 1:end]).strip():
            continue
        for a, b in windows_by_lines(lines[start - 1:end], start, size=MAX_LINES, overlap=3):
            chunks.append(Chunk(a, b, "\n".join(lines[a - 1:b]), title))
    return Extracted("L", max(len(lines), 1), chunks)


def extract_image_note(path: Path) -> Extracted:
    """Images have no text until Claude transcribes them (`study note-text`); keep the image for viewing."""
    return Extracted("p", 1, [], [Image(1, "note", path.resolve())], {"needs_transcription": True})


_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")


def _secs(m: re.Match) -> float:
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def parse_subtitles(path: Path) -> list[tuple[float, float, str]]:
    """SRT/VTT -> [(start, end, text)]."""
    segs, cur = [], None
    for ln in path.read_text(errors="replace").splitlines():
        ln = ln.strip()
        if "-->" in ln:
            ts = list(_TS.finditer(ln))
            if len(ts) >= 2:
                cur = [_secs(ts[0]), _secs(ts[1]), []]
                segs.append(cur)
            continue
        if not ln or ln.isdigit() or ln.startswith(("WEBVTT", "NOTE")) or cur is None:
            continue
        cur[2].append(re.sub(r"<[^>]+>", "", ln))
    return [(a, b, " ".join(t)) for a, b, t in segs if t]
