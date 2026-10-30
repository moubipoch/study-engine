"""Deterministic study planner.

For each day it fills the available hours with blocks, choosing courses by
    score = priority x need x urgency
  need     backlog of weeks not yet studied (no digest, or no practice yet) + weak topics, in hours
  urgency  1 without an exam date ("steady mode": keep up with the semester);
           1 + 10/sqrt(days left) with an exam date; the course is never planned after its exam.
Exam dates come only from course.yaml; a missing date is reported, never guessed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from study.config import Course

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ANKI_MIN = 20
DUE_MIN = 30
CATCHUP_H, PRACTICE_H = 1.5, 1.0
WEAK = 0.6
NEED_CAP_H = 4.0  # a huge backlog shouldn't drown out priorities: beyond ~one week of work, need saturates


class PlanError(ValueError):
    pass


@dataclass
class Settings:
    hours_per_day: dict
    day_start: str = "09:00"
    block_minutes: int = 90
    blocked_dates: list = field(default_factory=list)
    winter_start: dt.date | None = None
    winter_end: dt.date | None = None
    winter_hours: float = 6.0
    semester_start: dt.date | None = None

    def lectures_reached(self, course: Course, day: dt.date) -> int | None:
        """How many lectures the semester has reached by `day` (None = unknown: plan every week)."""
        if not self.semester_start:
            return None
        week = (day - self.semester_start).days // 7 + 1
        return max(0, week) * int(course.raw.get("lectures_per_week", 1))

    @classmethod
    def load(cls, path: Path) -> "Settings":
        raw = yaml.safe_load(path.read_text()) or {}
        wb = raw.get("winter_break") or {}

        def d(x):
            return dt.date.fromisoformat(str(x)) if x else None
        hpd = raw.get("hours_per_day") or {}
        if set(hpd) - set(WEEKDAYS):
            raise PlanError(f"hours_per_day keys must be {WEEKDAYS}")
        return cls({k: float(hpd.get(k, 0)) for k in WEEKDAYS}, str(raw.get("day_start", "09:00")),
                   int(raw.get("block_minutes", 90)), [d(x) for x in raw.get("blocked_dates") or []],
                   d(wb.get("start")), d(wb.get("end")), float(wb.get("hours_per_day", 6)), d(raw.get("semester_start")))

    def minutes(self, day: dt.date) -> int:
        if day in self.blocked_dates:
            return 0
        if self.winter_start and self.winter_end and self.winter_start <= day <= self.winter_end:
            return int(self.winter_hours * 60)
        return int(self.hours_per_day[WEEKDAYS[day.weekday()]] * 60)

    def is_winter(self, day: dt.date) -> bool:
        return bool(self.winter_start and self.winter_end and self.winter_start <= day <= self.winter_end)


@dataclass
class Task:
    course: str
    kind: str          # catchup | practice | weak | project | due | anki
    title: str
    hours: float
    week: int | None = None
    command: str = ""  # what to run in Claude/GUI to do it


@dataclass
class Block:
    date: str
    start: str
    end: str
    course: str
    kind: str
    title: str
    command: str = ""

    @property
    def uid(self) -> str:
        return hashlib.sha1(f"{self.date}|{self.start}|{self.course}|{self.title}".encode()).hexdigest()[:16] + "@study"


def course_tasks(con: sqlite3.Connection, course: Course, reached: int | None = None) -> list[Task]:
    """Backlog in study order: catch-up per week (oldest first), then practice of studied weeks, then weak topics."""
    from study.digest import todo
    from study.srs.mastery import mastery
    if course.project_only:
        return [Task(course.slug, "project", "Project work (AIS): literature, experiments, code review", 2.0,
                     command=f"Ask Claude: /project {course.slug}")]
    tasks: list[Task] = []
    practiced = {r[0] for r in con.execute(
        "SELECT DISTINCT q.lecture FROM attempts a JOIN questions q ON q.id=a.question_id WHERE q.course=?",
        (course.slug,))}
    unit = "week" if int(course.raw.get("lectures_per_week", 1)) == 1 else "lecture"
    lessons = {r["target"]: r["status"] for r in con.execute(
        "SELECT target, status FROM teach_sessions WHERE course=?", (course.slug,))}
    for it in todo(con, course.slug):
        if reached is not None and it.lecture > reached:
            continue  # not given yet this semester
        lesson = lessons.get(f"W{it.lecture:02d}")
        if lesson != "done":
            verb = "Continue the lesson on" if lesson == "active" else "Learn"
            tasks.append(Task(course.slug, "catchup", f"{verb} {unit} {it.lecture} (interactive lesson with Claude)",
                              CATCHUP_H, it.lecture, f"/teach {course.slug} {it.lecture}"))
        elif it.lecture not in practiced:
            tasks.append(Task(course.slug, "practice", f"Practise {unit} {it.lecture} (quiz, then grade)", PRACTICE_H,
                              it.lecture, f"/practice {course.slug} week {it.lecture}"))
    for m in mastery(con, course):
        if m["mastery"] is not None and m["mastery"] < WEAK:
            tasks.append(Task(course.slug, "weak", f"Strengthen '{m['topic']}' ({m['mastery']:.0%})", PRACTICE_H,
                              command=f'/practice {course.slug} topic "{m["topic"]}"'))
    return tasks


def _urgency(course: Course, day: dt.date) -> float | None:
    """None = don't plan this course on that day (exam over)."""
    if course.exam_date is None:
        return 1.0
    left = (course.exam_date - day).days
    if left < 0:
        return None
    return 1.0 + 10.0 / math.sqrt(max(left, 1))


def build_plan(con: sqlite3.Connection, courses: dict[str, Course], settings: Settings, start: dt.date,
               days: int = 7, busy: dict[str, int] | None = None) -> dict:
    """busy: {YYYY-MM-DD: minutes already taken (e.g. from the user's calendar)} -> less capacity that day."""
    from study.srs.fsrs_q import due
    queues = {slug: course_tasks(con, c, settings.lectures_reached(c, start + dt.timedelta(days=days - 1)))
              for slug, c in courses.items()}
    have_cards = con.execute("SELECT count(*) FROM cards").fetchone()[0] > 0
    due_by_course = {slug: len(due(con, slug)) for slug in courses}
    warnings = [f"{c.slug}: no exam date in course.yaml, so planning in steady mode (add exam_date for countdown mode)"
                for c in courses.values() if c.exam_date is None and not c.project_only]
    blocks: list[Block] = []
    used_week: dict[str, int] = {}  # fairness: each block a course got this week lowers its next score
    sh, sm = map(int, settings.day_start.split(":"))
    for i in range(days):
        day = start + dt.timedelta(days=i)
        cap = settings.minutes(day) - (busy or {}).get(day.isoformat(), 0)
        clock = dt.datetime.combine(day, dt.time(sh, sm))

        def add(minutes, course, kind, title, command=""):
            nonlocal clock, cap
            end = clock + dt.timedelta(minutes=minutes)
            blocks.append(Block(day.isoformat(), clock.strftime("%H:%M"), end.strftime("%H:%M"), course, kind, title, command))
            clock = end + dt.timedelta(minutes=10)  # short break between blocks
            cap -= minutes
        if cap <= 0:
            continue
        if have_cards and cap >= ANKI_MIN:
            add(ANKI_MIN, "all", "anki", "Anki reviews (all decks)", "Anki: study due cards")
        dues = sorted(((n, s) for s, n in due_by_course.items() if n), reverse=True)
        if dues and cap >= DUE_MIN and _urgency(courses[dues[0][1]], day) is not None:
            n, s = dues[0]
            add(DUE_MIN, s, "due", f"Re-practise {min(n, 3)} due questions", f"/practice {s} n={min(n, 3)}")
            due_by_course[s] = max(0, n - 3)
        used_today: dict[str, int] = {}
        if day.weekday() == 0:
            used_week.clear()
        while cap >= 30:
            best, best_score = None, 0.0
            for slug, c in courses.items():
                u = _urgency(c, day)
                if u is None:
                    continue
                q = queues[slug]
                need = min(sum(t.hours for t in q), NEED_CAP_H) if q else 0.3  # nothing pending: light maintenance
                score = c.priority * need * u / (1 + used_today.get(slug, 0) + 0.7 * used_week.get(slug, 0))
                if c.project_only:
                    score *= 1.0  # project-only: a steady ~weekly block, no exam pressure
                if score > best_score:
                    best, best_score = slug, score
            if best is None:
                break
            minutes = min(settings.block_minutes, cap)
            q = queues[best]
            if q:
                t = q[0]
                t.hours -= minutes / 60
                if t.hours <= 0.05:
                    q.pop(0)
                add(minutes, best, t.kind, t.title, t.command)
            else:
                add(minutes, best, "maintain", "Mixed practice / quiz on recent material", f"/quiz {best} n=4")
            used_today[best] = used_today.get(best, 0) + 1
            used_week[best] = used_week.get(best, 0) + 1
    winter = [b.date for b in blocks if settings.is_winter(dt.date.fromisoformat(b.date))]
    return {"start": start.isoformat(), "days": days, "blocks": [asdict(b) | {"uid": b.uid} for b in blocks],
            "warnings": warnings, "winter_days": sorted(set(winter)),
            "backlog_hours": {s: round(sum(t.hours for t in course_tasks(con, c, settings.lectures_reached(c, start))), 1)
                              for s, c in courses.items()}}


# ---------------------------------------------------------------- export

def to_ics(plan: dict, tz: str = "Europe/Zurich") -> str:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//study engine//planner//EN", "CALSCALE:GREGORIAN"]
    for b in plan["blocks"]:
        d = b["date"].replace("-", "")
        lines += ["BEGIN:VEVENT", f"UID:{b['uid']}", f"DTSTAMP:{stamp}",
                  f"DTSTART;TZID={tz}:{d}T{b['start'].replace(':', '')}00",
                  f"DTEND;TZID={tz}:{d}T{b['end'].replace(':', '')}00",
                  f"SUMMARY:{esc(b['course'].upper() + ': ' + b['title'])}",
                  f"DESCRIPTION:{esc(b['command'])}", "CATEGORIES:study", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_markdown(plan: dict) -> str:
    out = [f"# Study plan from {plan['start']} ({plan['days']} days)", ""]
    for w in plan["warnings"]:
        out.append(f"> {w}")
    day = None
    for b in plan["blocks"]:
        if b["date"] != day:
            day = b["date"]
            wd = dt.date.fromisoformat(day).strftime("%A")
            out += ["", f"## {wd} {day}" + ("  (winter break)" if day in plan["winter_days"] else "")]
        out.append(f"- {b['start']}–{b['end']} **{b['course']}** {b['title']}" + (f"  `{b['command']}`" if b["command"] else ""))
    return "\n".join(out) + "\n"
