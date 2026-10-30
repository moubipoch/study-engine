"""FSRS scheduling for bank questions (Anki schedules the flashcards itself)."""

from __future__ import annotations

import datetime as dt
import sqlite3

from fsrs import Card, Rating, Scheduler

_SCHED = Scheduler(desired_retention=0.9, enable_fuzzing=False)


def rating_for(score: float) -> Rating:
    """Graded score (0..1) -> FSRS rating. Partial credit on a long derivation is still a 'Hard' recall."""
    if score < 0.4:
        return Rating.Again
    if score < 0.7:
        return Rating.Hard
    if score < 0.9:
        return Rating.Good
    return Rating.Easy


def review(con: sqlite3.Connection, qid: str, score: float, when: dt.datetime | None = None) -> dt.datetime:
    when = when or dt.datetime.now(dt.timezone.utc)
    row = con.execute("SELECT fsrs FROM question_srs WHERE question_id=?", (qid,)).fetchone()
    card = Card.from_json(row["fsrs"]) if row else Card()
    card, _ = _SCHED.review_card(card, rating_for(score), review_datetime=when)
    with con:
        con.execute("INSERT OR REPLACE INTO question_srs(question_id,fsrs,due) VALUES (?,?,?)",
                    (qid, card.to_json(), card.due.isoformat()))
    return card.due


def retrievability(con: sqlite3.Connection, qid: str, now: dt.datetime | None = None) -> float | None:
    row = con.execute("SELECT fsrs FROM question_srs WHERE question_id=?", (qid,)).fetchone()
    if not row:
        return None
    return float(_SCHED.get_card_retrievability(Card.from_json(row["fsrs"]), now or dt.datetime.now(dt.timezone.utc)))


def due(con: sqlite3.Connection, course: str, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = con.execute("SELECT q.id, q.title, q.topic, q.lecture, s.due FROM question_srs s JOIN questions q"
                       " ON q.id=s.question_id WHERE q.course=? ORDER BY s.due", (course,)).fetchall()
    return [dict(r) for r in rows if dt.datetime.fromisoformat(r["due"]) <= now]
