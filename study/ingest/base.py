"""Shared types for extractors: each extractor turns one file into chunks (+ optional images)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chunk:
    loc_start: float
    loc_end: float
    text: str
    heading: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.loc_start}|{self.loc_end}|{self.heading}|{self.text}".encode()).hexdigest()[:16]


@dataclass
class Image:
    loc: float
    kind: str  # page | frame | note
    path: Path


@dataclass
class Extracted:
    loc_kind: str  # t | p | L
    extent: float
    chunks: list[Chunk]
    images: list[Image] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(bufsize):
            h.update(b)
    return h.hexdigest()


def windows_by_lines(lines: list[str], start_line: int, size: int = 60, overlap: int = 5) -> list[tuple[int, int]]:
    """Split a 1-based line range into windows [(first, last)] of at most `size` lines."""
    n = len(lines)
    out, i = [], 0
    while i < n:
        j = min(n, i + size)
        out.append((start_line + i, start_line + j - 1))
        if j == n:
            break
        i = j - overlap
    return out
