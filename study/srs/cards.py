"""Flashcards: the local source of truth, mirrored into Anki (which owns their review schedule)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from study.cite import check_text, find_cites


class CardError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def card_id(course: str, front: str) -> str:
    """Stable id from the question side: editing the answer keeps the same Anki note."""
    return "c-" + hashlib.sha1(f"{course}|{_norm(front)}".encode()).hexdigest()[:14]


def _hash(c: dict) -> str:
    return hashlib.sha1(json.dumps([c["front"], c["back"], c.get("cite", ""), c.get("topic", ""),
                                    sorted(c.get("tags") or [])]).encode()).hexdigest()[:16]


def add_cards(con: sqlite3.Connection, course: str, items: list[dict]) -> dict:
    """Validate and upsert cards. Every card needs a resolvable citation. Returns counts + errors."""
    added = updated = unchanged = 0
    errors = []
    for i, it in enumerate(items):
        front, back = str(it.get("front", "")).strip(), str(it.get("back", "")).strip()
        cite = str(it.get("cite", "")).strip()
        if not front or not back:
            errors.append(f"#{i}: front and back are required")
            continue
        if not find_cites(cite):
            errors.append(f"#{i} ({front[:40]}): needs a citation [@course:key#loc]")
            continue
        _, cerr = check_text(con, cite + " " + front + " " + back, course)
        if cerr:
            errors.append(f"#{i} ({front[:40]}): " + "; ".join(f"{e.cite} {e.reason}" for e in cerr))
            continue
        c = {"front": front, "back": back, "cite": cite, "topic": str(it.get("topic", "")),
             "lecture": it.get("lecture"), "tags": list(it.get("tags") or [])}
        cid, h = card_id(course, front), _hash(c)
        row = con.execute("SELECT content_hash FROM cards WHERE id=?", (cid,)).fetchone()
        with con:
            if row is None:
                con.execute("INSERT INTO cards(id,course,topic,lecture,front,back,cite,tags,content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                            (cid, course, c["topic"], c["lecture"], front, back, cite, json.dumps(c["tags"]), h))
                added += 1
            elif row["content_hash"] != h:
                con.execute("UPDATE cards SET topic=?,lecture=?,back=?,cite=?,tags=?,content_hash=? WHERE id=?",
                            (c["topic"], c["lecture"], back, cite, json.dumps(c["tags"]), h, cid))
                updated += 1
            else:
                unchanged += 1
    return {"added": added, "updated": updated, "unchanged": unchanged, "errors": errors}


def edit_card(con: sqlite3.Connection, cid: str, front: str | None = None, back: str | None = None) -> None:
    row = con.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    if not row:
        raise CardError(f"unknown card {cid}")
    c = dict(row) | {"tags": json.loads(row["tags"])}
    if front:
        c["front"] = front.strip()
    if back:
        c["back"] = back.strip()
    with con:
        con.execute("UPDATE cards SET front=?, back=?, content_hash=? WHERE id=?", (c["front"], c["back"], _hash(c), cid))


def list_cards(con: sqlite3.Connection, course: str) -> list[dict]:
    rows = con.execute(
        "SELECT c.*, s.reps, s.lapses, s.interval, s.flags, s.due FROM cards c LEFT JOIN anki_state s ON s.card_id=c.id"
        " WHERE c.course=? ORDER BY c.lecture, c.topic, c.front", (course,)).fetchall()
    return [dict(r) | {"tags": json.loads(r["tags"])} for r in rows]


# ---------------------------------------------------------------- Anki rendering

_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
_INLINE = re.compile(r"(?<![\\$])\$(?!\s)(.+?)(?<!\s)\$(?!\d)", re.S)


def to_anki_html(md: str) -> str:
    """Markdown + $…$ LaTeX -> Anki HTML with MathJax delimiters \\( \\) and \\[ \\]."""
    import html as _html

    import markdown2
    keep: list[str] = []

    def stash(s: str) -> str:
        keep.append(s)
        return f"\x00{len(keep) - 1}\x00"
    md = _DISPLAY.sub(lambda m: stash(r"\[" + _html.escape(m.group(1).strip(), quote=False) + r"\]"), md)
    md = _INLINE.sub(lambda m: stash(r"\(" + _html.escape(m.group(1), quote=False) + r"\)"), md)
    out = markdown2.markdown(md, extras=["fenced-code-blocks", "tables", "break-on-newline"]).strip()
    if out.startswith("<p>") and out.endswith("</p>") and out.count("<p>") == 1:
        out = out[3:-4]
    return re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], out)
