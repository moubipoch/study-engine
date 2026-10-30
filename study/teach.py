"""Interactive teaching sessions (/teach): the deterministic half.

Claude plans a lesson (an ordered list of concepts, each with citations), then teaches one concept per turn
and asks a check question. This module stores the plan, where the user is, what they struggled with, and
the check results (which feed topic mastery), so a lesson can be resumed days later.
"""

from __future__ import annotations

import json
import re
import sqlite3

from study.cite import check_text


class TeachError(ValueError):
    pass


def norm_target(target: str) -> str:
    """'3', 'W3', 'w03', 'L3' -> 'W03'; anything else is a topic -> 'topic-<slug>'."""
    t = target.strip()
    m = re.fullmatch(r"[WwLl]?(\d{1,2})", t)
    if m:
        return f"W{int(m.group(1)):02d}"
    if t.startswith("topic-"):
        return t
    slug = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    if not slug:
        raise TeachError(f"empty target {target!r}")
    return f"topic-{slug[:60]}"


def week_of(target: str) -> int | None:
    return int(target[1:]) if re.fullmatch(r"W\d+", target) else None


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["plan"] = json.loads(d["plan"])
    d["struggles"] = json.loads(d["struggles"])
    d["total"] = len(d["plan"])
    return d


def get(con: sqlite3.Connection, course: str, target: str) -> dict | None:
    s = _row(con.execute("SELECT * FROM teach_sessions WHERE course=? AND target=?",
                         (course, norm_target(target))).fetchone())
    if s:
        s["checks"] = [dict(r) for r in con.execute(
            "SELECT concept, topic, score, ts FROM teach_checks WHERE course=? AND target=? ORDER BY id",
            (course, s["target"]))]
    return s


def save_plan(con: sqlite3.Connection, course: str, target: str, title: str, plan: list[dict]) -> dict:
    """Create or replace a lesson plan. Each concept needs a name and at least one valid citation.

    Replacing a plan keeps the position when the concepts already taught are unchanged (re-planning the rest)."""
    target = norm_target(target)
    if not plan:
        raise TeachError("the plan has no concepts")
    errors = []
    clean = []
    for i, c in enumerate(plan):
        name = (c.get("name") or "").strip()
        if not name:
            errors.append(f"concept {i + 1}: missing name")
            continue
        cites = c.get("cites") or []
        if isinstance(cites, str):
            cites = [cites]
        found, bad = check_text(con, " ".join(cites), course)
        if not found:
            errors.append(f"{name}: needs at least one citation like [@{course}:key#loc]")
        errors += [f"{name}: {e.cite}: {e.reason}" for e in bad]
        clean.append({"name": name, "topic": c.get("topic"), "cites": cites, "why": c.get("why", "")})
    if errors:
        raise TeachError("plan rejected:\n  " + "\n  ".join(errors))
    old = get(con, course, target)
    position = 0
    if old:
        done = [c["name"] for c in old["plan"][:old["position"]]]
        if done == [c["name"] for c in clean[:len(done)]]:
            position = len(done)
    with con:
        con.execute(
            "INSERT INTO teach_sessions(course,target,title,plan,position,status) VALUES (?,?,?,?,?,'active')"
            " ON CONFLICT(course,target) DO UPDATE SET title=excluded.title, plan=excluded.plan,"
            " position=excluded.position, status='active', updated=datetime('now')",
            (course, target, title, json.dumps(clean), position))
    return get(con, course, target)


def progress(con: sqlite3.Connection, course: str, target: str, *, position: int | None = None,
             advance: bool = False, struggle: str | None = None, concept: str | None = None,
             done: bool = False, claude_session: str | None = None) -> dict:
    s = get(con, course, target)
    if not s:
        raise TeachError(f"no lesson for {course} {norm_target(target)}: save a plan first")
    pos = s["position"]
    if advance:
        pos += 1
    if position is not None:
        pos = position
    pos = max(0, min(pos, s["total"]))
    struggles = s["struggles"]
    if struggle:
        cur = concept or (s["plan"][min(s["position"], s["total"] - 1)]["name"])
        struggles.append({"concept": cur, "note": struggle})
    status = "done" if done or pos >= s["total"] else "active"
    with con:
        con.execute("UPDATE teach_sessions SET position=?, struggles=?, status=?, updated=datetime('now'),"
                    " claude_session=COALESCE(?, claude_session) WHERE course=? AND target=?",
                    (pos, json.dumps(struggles), status, claude_session, course, s["target"]))
    return get(con, course, target)


def set_claude_session(con: sqlite3.Connection, course: str, target: str, session_id: str) -> None:
    with con:
        con.execute("UPDATE teach_sessions SET claude_session=? WHERE course=? AND target=?",
                    (session_id, course, norm_target(target)))


def log_check(con: sqlite3.Connection, course: str, target: str, score: float, concept: str | None = None,
              topic: str | None = None) -> dict:
    """Record a check-question result (0, 0.5 or 1). Concept/topic default to the current concept."""
    s = get(con, course, target)
    if not s:
        raise TeachError(f"no lesson for {course} {norm_target(target)}")
    if not 0 <= score <= 1:
        raise TeachError("score must be between 0 and 1")
    cur = s["plan"][min(s["position"], s["total"] - 1)]
    concept = concept or cur["name"]
    if topic is None:
        topic = next((c.get("topic") for c in s["plan"] if c["name"] == concept), None)
    with con:
        con.execute("INSERT INTO teach_checks(course,target,concept,topic,score) VALUES (?,?,?,?,?)",
                    (course, s["target"], concept, topic, score))
    return {"concept": concept, "topic": topic, "score": score}


def listing(con: sqlite3.Connection, course: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM teach_sessions", []
    if course:
        q, a = q + " WHERE course=?", [course]
    out = []
    for r in con.execute(q + " ORDER BY updated DESC", a):
        d = _row(r)
        sc = con.execute("SELECT avg(score) s, count(*) n FROM teach_checks WHERE course=? AND target=?",
                         (d["course"], d["target"])).fetchone()
        out.append({k: d[k] for k in ("course", "target", "title", "position", "total", "status", "updated",
                                      "claude_session")}
                   | {"next": d["plan"][d["position"]]["name"] if d["position"] < d["total"] else None,
                      "checks": sc["n"], "check_score": sc["s"], "struggles": len(d["struggles"])})
    return out
