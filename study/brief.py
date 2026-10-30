"""Lecture overviews ("briefs"): a one-screen understanding of a week, the starting point for follow-ups.

Shorter than a digest. Same caching rule: regenerated only when the week's material changes."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path

import yaml

from study.cite import check_text
from study.config import Course
from study.digest import _strip_frontmatter, input_hash, lecture_sources, todo

MIN_CITES = 3
MAX_CHARS = 7000  # about one screen and a half; longer is a digest, not an overview


class BriefError(ValueError):
    pass


def brief_path(course: Course, lecture: int) -> Path:
    return course.dir / "briefs" / f"L{lecture:02d}.md"


def status(con: sqlite3.Connection, course: str) -> dict[int, str]:
    """week -> missing | stale | fresh, for every week that has lecture material."""
    out = {}
    for it in todo(con, course):
        art = con.execute("SELECT input_hash, path FROM artifacts WHERE kind='brief' AND course=? AND target=?",
                          (course, str(it.lecture))).fetchone()
        if art is None or not Path(art["path"]).exists():
            out[it.lecture] = "missing"
        else:
            out[it.lecture] = "fresh" if art["input_hash"] == input_hash(con, course, it.lecture) else "stale"
    return out


def follow_ups(text: str) -> list[str]:
    """The suggested questions under '## Go deeper' (numbered or bulleted)."""
    m = re.search(r"^## Go deeper\s*$(.*?)(?=^## |\Z)", _strip_frontmatter(text), re.M | re.S)
    if not m:
        return []
    qs = [re.sub(r"\s*\[@[^\]]+\]", "", q).strip() for q in re.findall(r"^\s*(?:\d+[.)]|[-*])\s+(.+)$", m.group(1), re.M)]
    return [q.strip("*_ ") for q in qs if q][:8]


def save(con: sqlite3.Connection, course: Course, lecture: int, text: str) -> tuple[Path, dict]:
    if not lecture_sources(con, course.slug, lecture):
        raise BriefError(f"no lecture material for {course.slug} week {lecture}")
    body = _strip_frontmatter(text).strip()
    errors, warnings = [], []
    h1 = re.search(r"^# (.+)$", body, re.M)
    if not h1:
        errors.append("missing '# W<N>: <title>' heading")
    cites, bad = check_text(con, body, course.slug)
    errors += [f"{e.cite}: {e.reason}" for e in bad]
    if len(cites) < MIN_CITES:
        errors.append(f"only {len(cites)} citations (need at least {MIN_CITES})")
    if not follow_ups(body):
        errors.append("missing '## Go deeper' with suggested follow-up questions")
    if len(body) > MAX_CHARS:
        warnings.append(f"{len(body)} characters: an overview should fit on about one screen")
    if errors:
        raise BriefError("overview rejected:\n  - " + "\n  - ".join(errors))
    ih = input_hash(con, course.slug, lecture)
    title = h1.group(1).strip()
    fm = {"course": course.slug, "lecture": lecture, "title": title, "input_hash": ih,
          "generated": dt.date.today().isoformat(), "tags": [f"course/{course.slug}", "overview"]}
    path = brief_path(course, lecture)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body + "\n")
    with con:
        con.execute("INSERT OR REPLACE INTO artifacts(kind,course,target,input_hash,path,meta) VALUES ('brief',?,?,?,?,'{}')",
                    (course.slug, str(lecture), ih, str(path)))
    return path, {"path": str(path), "title": title, "cites": len(cites), "follow_ups": len(follow_ups(body)),
                  "warnings": warnings}
