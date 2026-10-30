"""Per-lecture digests: staleness tracking (content-hash cache), validation, storage, glossary harvesting.

Claude writes the digest (/digest); this module decides *what* needs (re)writing and refuses digests
that break the schema or cite locations that don't exist.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from study.cite import check_text
from study.config import Course

SCHEMA_VERSION = 1  # bump when the digest structure changes -> every digest becomes stale
# Material that defines a lecture. Your own code/notes don't make a digest stale.
DIGEST_SOURCE_TYPES = ("slides", "script", "recording", "exercise")
REQUIRED_SECTIONS = ["Overview", "Concepts", "Glossary", "Key results", "What an exam would ask"]
CONCEPT_PARTS = ["Intuition", "Example", "Formal", "Pitfalls", "Connections"]
MIN_CITES = 5
CHARS_PER_TOKEN = 4


class DigestError(ValueError):
    pass


def lecture_sources(con: sqlite3.Connection, course: str, lecture: int) -> list[sqlite3.Row]:
    q = (f"SELECT * FROM sources WHERE course=? AND lecture=? AND type IN ({','.join('?' * len(DIGEST_SOURCE_TYPES))})"
         " ORDER BY type, key")
    return con.execute(q, (course, lecture, *DIGEST_SOURCE_TYPES)).fetchall()


def input_hash(con: sqlite3.Connection, course: str, lecture: int) -> str:
    """Hash of every chunk the lecture's digest is built from (+ schema version)."""
    h = hashlib.sha256(f"digest-v{SCHEMA_VERSION}".encode())
    for s in lecture_sources(con, course, lecture):
        h.update(f"|{s['key']}|".encode())
        for (ch,) in con.execute("SELECT content_hash FROM chunks WHERE source_id=? ORDER BY ord", (s["id"],)):
            h.update(ch.encode())
    return h.hexdigest()[:20]


@dataclass
class TodoItem:
    lecture: int
    status: str  # missing | stale | fresh
    sources: list[str]
    approx_tokens: int
    path: str | None = None


def todo(con: sqlite3.Connection, course: str) -> list[TodoItem]:
    lectures = [r[0] for r in con.execute(
        f"SELECT DISTINCT lecture FROM sources WHERE course=? AND lecture IS NOT NULL"
        f" AND type IN ({','.join('?' * len(DIGEST_SOURCE_TYPES))}) ORDER BY lecture", (course, *DIGEST_SOURCE_TYPES))]
    out = []
    for n in lectures:
        srcs = lecture_sources(con, course, n)
        chars = sum(con.execute("SELECT coalesce(sum(length(text)),0) FROM chunks WHERE source_id=?",
                                (s["id"],)).fetchone()[0] for s in srcs)
        art = con.execute("SELECT input_hash, path FROM artifacts WHERE kind='digest' AND course=? AND target=?",
                          (course, str(n))).fetchone()
        if art is None or not Path(art["path"]).exists():
            status = "missing"
        elif art["input_hash"] != input_hash(con, course, n):
            status = "stale"
        else:
            status = "fresh"
        out.append(TodoItem(n, status, [s["key"] for s in srcs], chars // CHARS_PER_TOKEN, art["path"] if art else None))
    return out


# ---------------------------------------------------------------- validation

_H1 = re.compile(r"^# (.+)$", re.M)
_H2 = re.compile(r"^## (.+?)\s*$", re.M)
_ENTRY = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*\s*[:—–-]\s*(.+)$", re.M)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S", re.M)


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def sections(body: str) -> dict[str, str]:
    """H2 title -> section text."""
    marks = list(_H2.finditer(body))
    out = {}
    for m, nxt in zip(marks, marks[1:] + [None]):
        out[m.group(1).strip()] = body[m.end():nxt.start() if nxt else len(body)].strip()
    return out


def _find_section(secs: dict[str, str], name: str) -> str | None:
    for k, v in secs.items():
        if k.lower().startswith(name.lower()):
            return v
    return None


@dataclass
class Validation:
    title: str
    cites: int
    glossary: list[tuple[str, str]]
    key_results: list[tuple[str, str]]
    exam_questions: int
    warnings: list[str] = field(default_factory=list)


def validate(con: sqlite3.Connection, course: str, text: str) -> Validation:
    body = _strip_frontmatter(text)
    errors, warnings = [], []
    h1 = _H1.search(body)
    if not h1:
        errors.append("missing a '# Title' heading")
    secs = sections(body)
    for name in REQUIRED_SECTIONS:
        if _find_section(secs, name) is None:
            errors.append(f"missing section '## {name}'")
    cites, cite_errors = check_text(con, body, course)
    errors += [f"bad citation {e.cite}: {e.reason}" for e in cite_errors]
    if len(cites) < MIN_CITES:
        errors.append(f"only {len(cites)} citations; a digest needs at least {MIN_CITES}")
    concepts = _find_section(secs, "Concepts") or ""
    blocks = re.split(r"^### ", concepts, flags=re.M)[1:]
    if not blocks:
        errors.append("'## Concepts' needs at least one '### Concept' subsection")
    for b in blocks:
        name = b.splitlines()[0].strip()
        missing = [p for p in CONCEPT_PARTS if not re.search(rf"\*\*{p}\b", b)]
        if missing:
            warnings.append(f"concept '{name}' lacks: {', '.join(missing)}")
    gloss = [(t.strip(), d.strip()) for t, d in _ENTRY.findall(_find_section(secs, "Glossary") or "")]
    results = [(t.strip(), d.strip()) for t, d in _ENTRY.findall(_find_section(secs, "Key results") or "")]
    for name, parsed in (("Glossary", gloss), ("Key results", results)):
        bullets = len(re.findall(r"^\s*[-*]\s+\S", _find_section(secs, name) or "", re.M))
        if bullets > len(parsed):
            errors.append(f"{bullets - len(parsed)} '{name}' bullet(s) not in the form '- **name**: text' "
                          "(they would be dropped from the course index)")
    if not gloss:
        errors.append("glossary entries must look like '- **term**: definition [@cite]'")
    if not results:
        errors.append("key results must look like '- **name**: statement [@cite]'")
    n_q = len(_NUMBERED.findall(_find_section(secs, "What an exam would ask") or ""))
    if n_q < 3:
        errors.append(f"'What an exam would ask' needs at least 3 numbered questions (found {n_q})")
    if errors:
        raise DigestError("digest rejected:\n  - " + "\n  - ".join(errors))
    return Validation(h1.group(1).strip(), len(cites), gloss, results, n_q, warnings)


# ---------------------------------------------------------------- saving

def digest_path(course: Course, lecture: int) -> Path:
    return course.dir / "digests" / f"L{lecture:02d}.md"


_PART_START = re.compile(r"(?<!\n)\n(\*\*(?:" + "|".join(CONCEPT_PARTS) + r")\b)")


def tidy(body: str) -> str:
    """Each concept part (**Intuition**, **Example**, …) starts its own paragraph, outside code fences."""
    parts = re.split(r"(^```.*?^```)", body, flags=re.M | re.S)
    return "".join(p if p.startswith("```") else _PART_START.sub(r"\n\n\1", p) for p in parts)


def save(con: sqlite3.Connection, course: Course, lecture: int, text: str) -> tuple[Path, Validation]:
    srcs = lecture_sources(con, course.slug, lecture)
    if not srcs:
        raise DigestError(f"no lecture material for {course.slug} week {lecture}")
    v = validate(con, course.slug, text)
    ih = input_hash(con, course.slug, lecture)
    fm = {"course": course.slug, "lecture": lecture, "title": v.title, "sources": [s["key"] for s in srcs],
          "input_hash": ih, "schema": SCHEMA_VERSION, "generated": dt.date.today().isoformat(),
          "tags": [f"course/{course.slug}", "digest"]}
    out = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + tidy(_strip_frontmatter(text).strip()) + "\n"
    path = digest_path(course, lecture)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out)
    with con:
        con.execute("INSERT OR REPLACE INTO artifacts(kind,course,target,input_hash,path,meta) VALUES ('digest',?,?,?,?,?)",
                    (course.slug, str(lecture), ih, str(path),
                     json.dumps({"title": v.title, "cites": v.cites, "warnings": v.warnings})))
        con.execute("DELETE FROM glossary WHERE course=? AND lecture=?", (course.slug, lecture))
        con.executemany("INSERT OR REPLACE INTO glossary VALUES (?,?,?,?)",
                        [(course.slug, lecture, t, d) for t, d in v.glossary])
        con.execute("DELETE FROM key_results WHERE course=? AND lecture=?", (course.slug, lecture))
        con.executemany("INSERT INTO key_results VALUES (?,?,?,?,?)",
                        [(course.slug, lecture, i, t, d) for i, (t, d) in enumerate(v.key_results)])
    write_course_indexes(con, course)
    return path, v


def write_course_indexes(con: sqlite3.Connection, course: Course) -> None:
    """Deterministic course-wide glossary.md and key_results.md, rebuilt from all digests (no LLM)."""
    rows = con.execute("SELECT lecture, term, definition FROM glossary WHERE course=? ORDER BY lower(term), lecture",
                       (course.slug,)).fetchall()
    lines = [f"# Glossary: {course.name}", "", "_Generated from the lecture digests; edit the digests, not this file._", ""]
    for r in rows:
        lines.append(f"- **{r['term']}** ([W{r['lecture']}](digests/L{r['lecture']:02d}.md)): {r['definition']}")
    (course.dir / "glossary.md").write_text("\n".join(lines) + "\n")

    rows = con.execute("SELECT lecture, name, statement FROM key_results WHERE course=? ORDER BY lecture, ord",
                       (course.slug,)).fetchall()
    lines = [f"# Key results: {course.name}", "", "_Generated from the lecture digests._"]
    current = None
    for r in rows:
        if r["lecture"] != current:
            current = r["lecture"]
            lines += ["", f"## Week {current} ([digest](digests/L{current:02d}.md))", ""]
        lines.append(f"- **{r['name']}**: {r['statement']}")
    (course.dir / "key_results.md").write_text("\n".join(lines) + "\n")
