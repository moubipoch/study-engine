"""Topic lists: Claude proposes (with evidence), the user approves. Stored in course.yaml (managed part)."""

from __future__ import annotations

import sqlite3

from study.cite import check_text
from study.config import Course, topic_name, update_course_yaml


class TopicError(ValueError):
    pass


def normalise(items: list) -> list[dict]:
    """Accept strings or dicts; return dicts {name, summary, lectures, cites} with unique names."""
    out, seen = [], set()
    for it in items:
        d = {"name": it} if isinstance(it, str) else dict(it)
        name = str(d.get("name", "")).strip()
        if not name:
            raise TopicError(f"topic without a name: {it!r}")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        d["name"] = name
        d.setdefault("summary", "")
        d["lectures"] = [int(x) for x in d.get("lectures") or []]
        d["cites"] = [str(c) for c in d.get("cites") or []]
        out.append({k: d[k] for k in ("name", "summary", "lectures", "cites")} |
                   {k: v for k, v in d.items() if k not in ("name", "summary", "lectures", "cites")})
    return out


def propose(con: sqlite3.Connection, course: Course, items: list) -> list[dict]:
    """Replace the proposal list. Every evidence citation must resolve."""
    topics = normalise(items)
    bad = []
    for t in topics:
        _, errors = check_text(con, " ".join(t["cites"]), course.slug)
        bad += [f"{t['name']}: {e.cite} -> {e.reason}" for e in errors]
    if bad:
        raise TopicError("invalid citations:\n  " + "\n  ".join(bad))
    update_course_yaml(course, topics_proposed=topics)
    return topics


def approve(course: Course, names: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Move proposed topics (all, or those named) into `topics`. Returns (approved, still_proposed)."""
    proposed = normalise(course.topics_proposed)
    wanted = {n.lower() for n in names} if names else None
    if wanted is not None:
        unknown = wanted - {t["name"].lower() for t in proposed}
        if unknown:
            raise TopicError(f"not in the proposal: {sorted(unknown)}")
    take = [t for t in proposed if wanted is None or t["name"].lower() in wanted]
    keep = [t for t in proposed if t not in take]
    current = normalise(course.topics)
    have = {t["name"].lower() for t in current}
    merged = current + [t for t in take if t["name"].lower() not in have]
    update_course_yaml(course, topics=merged, topics_proposed=keep)
    return [t["name"] for t in take], [t["name"] for t in keep]


def remove(course: Course, names: list[str]) -> list[str]:
    drop = {n.lower() for n in names}
    current = normalise(course.topics)
    update_course_yaml(course, topics=[t for t in current if t["name"].lower() not in drop])
    return [topic_name(t) for t in current if t["name"].lower() in drop]


def set_topics(course: Course, items: list) -> list[dict]:
    """Replace the approved list wholesale (GUI editing: rename/reorder)."""
    topics = normalise(items)
    update_course_yaml(course, topics=topics)
    return topics
