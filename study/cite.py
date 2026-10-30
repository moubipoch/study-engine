"""Machine-checkable citations: [@course:source_key#loc] with loc = t=HH:MM:SS | pN | LA-B."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

CITE_RE = re.compile(r"\[@(?P<course>[a-z0-9_-]+):(?P<key>[^#\]\s]+)#(?P<loc>[^\]\s]+)\]")
_T = re.compile(r"t=(?:(\d+):)?(\d{1,2}):(\d{2})$")
_P = re.compile(r"p(\d+)(?:-(\d+))?$")
_L = re.compile(r"L(\d+)(?:-(\d+))?$")


@dataclass
class Cite:
    raw: str
    course: str
    key: str
    loc: str
    kind: str | None = None   # t | p | L
    start: float | None = None
    end: float | None = None


@dataclass
class CiteError:
    cite: str
    reason: str


def parse_loc(loc: str) -> tuple[str, float, float] | None:
    if m := _T.match(loc):
        h, mi, s = m.groups()
        t = int(h or 0) * 3600 + int(mi) * 60 + int(s)
        return "t", t, t
    if m := _P.match(loc):
        a = int(m.group(1))
        return "p", a, int(m.group(2) or a)
    if m := _L.match(loc):
        a = int(m.group(1))
        return "L", a, int(m.group(2) or a)
    return None


def find_cites(text: str) -> list[Cite]:
    out = []
    for m in CITE_RE.finditer(text):
        c = Cite(m.group(0), m.group("course"), m.group("key"), m.group("loc"))
        if parsed := parse_loc(c.loc):
            c.kind, c.start, c.end = parsed
        out.append(c)
    return out


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def make_cite(course: str, key: str, loc_kind: str, start: float, end: float | None = None) -> str:
    if loc_kind == "t":
        return f"[@{course}:{key}#t={fmt_time(start)}]"
    a, b = int(start), int(end if end is not None else start)
    prefix = "p" if loc_kind == "p" else "L"
    return f"[@{course}:{key}#{prefix}{a}{'' if a == b else f'-{b}'}]"


def check_cite(con: sqlite3.Connection, c: Cite, course: str | None = None) -> str | None:
    """Return None if the citation resolves, else the reason it doesn't."""
    if course and c.course != course:
        return f"cites course {c.course!r}, expected {course!r}"
    if c.kind is None:
        return f"malformed location {c.loc!r} (use t=HH:MM:SS, pN, or LA-B)"
    row = con.execute("SELECT loc_kind, extent FROM sources WHERE course=? AND key=?", (c.course, c.key)).fetchone()
    if row is None:
        return f"unknown source {c.course}:{c.key}"
    if row["loc_kind"] != c.kind:
        want = {"t": "t=HH:MM:SS", "p": "pN", "L": "LA-B"}[row["loc_kind"]]
        return f"{c.key} is located by {want}, not {c.loc!r}"
    if c.start > c.end:
        return f"range {c.loc!r} is reversed"
    lo = 0 if c.kind == "t" else 1
    if c.start < lo or c.end > row["extent"] + (1 if c.kind == "t" else 0):
        unit = {"t": f"{fmt_time(row['extent'])} long", "p": f"{int(row['extent'])} pages",
                "L": f"{int(row['extent'])} lines"}[c.kind]
        return f"{c.loc} out of range ({c.key} is {unit})"
    return None


def check_text(con: sqlite3.Connection, text: str, course: str | None = None) -> tuple[list[Cite], list[CiteError]]:
    cites = find_cites(text)
    errors = [CiteError(c.raw, r) for c in cites if (r := check_cite(con, c, course))]
    # catch near-miss citation syntax that the regex didn't accept
    for m in re.finditer(r"\[@[^\]]*\]", text):
        if not CITE_RE.fullmatch(m.group(0)):
            errors.append(CiteError(m.group(0), "malformed citation (expected [@course:key#loc])"))
    return cites, errors


def resolve(con: sqlite3.Connection, c: Cite, context: int = 0) -> list[sqlite3.Row]:
    """Chunks overlapping the cited location (plus `context` neighbours on each side)."""
    rows = con.execute(
        "SELECT ch.*, s.key, s.course, s.loc_kind FROM chunks ch JOIN sources s ON s.id=ch.source_id"
        " WHERE s.course=? AND s.key=? ORDER BY ch.ord", (c.course, c.key)).fetchall()
    hit = [i for i, r in enumerate(rows) if r["loc_start"] <= c.end and r["loc_end"] >= c.start]
    if not hit:
        return []
    return rows[max(0, hit[0] - context): hit[-1] + 1 + context]
