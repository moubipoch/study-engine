"""Per-topic mastery from graded practice and /teach check questions (recency-weighted) and Anki reviews
(recent recall rate)."""

from __future__ import annotations

import datetime as dt
import math
import sqlite3

from study.config import Course, topic_name

HALF_LIFE_DAYS = 21          # an attempt from 3 weeks ago counts half as much as one from today
CARD_WINDOW_DAYS = 30        # Anki: recall rate over the last 30 days of reviews
W_QUESTIONS, W_CARDS = 0.6, 0.4
W_TEACH_CHECK = 0.5     # a /teach check question counts half as much as a graded exam-style question


def _age_days(ts: str, now: dt.datetime) -> float:
    t = dt.datetime.fromisoformat(ts.replace(" ", "T"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (now - t).total_seconds() / 86400)


def _key(topic: str) -> str:
    return " ".join(topic.lower().split())


def mastery(con: sqlite3.Connection, course: Course, now: dt.datetime | None = None) -> list[dict]:
    """One row per topic: question score, card recall, combined (0..1 or None when there is no evidence)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    topics: dict[str, dict] = {}

    def row(name: str) -> dict:
        return topics.setdefault(_key(name), {"topic": name, "q_w": 0.0, "q_s": 0.0, "q_n": 0, "c_ok": 0, "c_n": 0,
                                              "questions": 0, "cards": 0})

    for t in course.topics:
        row(topic_name(t))
    for r in con.execute("SELECT topic, count(*) n FROM questions WHERE course=? GROUP BY topic", (course.slug,)):
        row(r["topic"] or "(no topic)")["questions"] = r["n"]
    for r in con.execute("SELECT topic, count(*) n FROM cards WHERE course=? GROUP BY topic", (course.slug,)):
        row(r["topic"] or "(no topic)")["cards"] = r["n"]
    for r in con.execute("SELECT q.topic, a.score, a.ts FROM attempts a JOIN questions q ON q.id=a.question_id"
                         " WHERE q.course=?", (course.slug,)):
        t = row(r["topic"] or "(no topic)")
        w = math.pow(0.5, _age_days(r["ts"], now) / HALF_LIFE_DAYS)
        t["q_w"] += w
        t["q_s"] += w * r["score"]
        t["q_n"] += 1
    for r in con.execute("SELECT topic, score, ts FROM teach_checks WHERE course=?", (course.slug,)):
        t = row(r["topic"] or "(no topic)")
        w = W_TEACH_CHECK * math.pow(0.5, _age_days(r["ts"], now) / HALF_LIFE_DAYS)
        t["q_w"] += w
        t["q_s"] += w * r["score"]
        t["q_n"] += 1
    cutoff = (now - dt.timedelta(days=CARD_WINDOW_DAYS)).isoformat()
    for r in con.execute("SELECT c.topic, rv.ease FROM anki_reviews rv JOIN cards c ON c.id=rv.card_id"
                         " WHERE c.course=? AND rv.ts>=?", (course.slug, cutoff)):
        t = row(r["topic"] or "(no topic)")
        t["c_n"] += 1
        t["c_ok"] += r["ease"] > 1
    out = []
    for t in topics.values():
        q = t["q_s"] / t["q_w"] if t["q_w"] > 0 else None
        c = t["c_ok"] / t["c_n"] if t["c_n"] else None
        combined = (W_QUESTIONS * q + W_CARDS * c) if q is not None and c is not None else (q if q is not None else c)
        out.append({"topic": t["topic"], "questions": t["questions"], "cards": t["cards"], "attempts": t["q_n"],
                    "reviews": t["c_n"], "question_score": q, "card_recall": c, "mastery": combined})
    # weakest first; topics without evidence at the end (they need practice, but we can't rank them)
    out.sort(key=lambda r: (r["mastery"] is None, r["mastery"] if r["mastery"] is not None else 0, r["topic"]))
    return out
