"""The knowledge vault (Obsidian + git): what you know, measured against what each exam rewards.

    StudyVault/
      Home.md                       dashboard
      Courses/<C>/<C>.md            course hub: exam rules, target, links
      Courses/<C>/Exam model.md     topics with their share of exam points (frontmatter `topics:`)
      Courses/<C>/Readiness.md      generated: expected exam score, per topic, next best actions, history
      Concepts/<name>.md            one node per concept you have studied (frontmatter: course, topic, mastery …)
      Sessions/<date> <C>.md        one summary per study session
      Library/                      generated reference notes (study export)

Everything here is deterministic. Claude writes the concept nodes, exam models and session notes (/wrap,
/exam-model); this module turns them into readiness numbers and a ranked "what earns the most points next".
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BASE_HALF_LIFE = 7.0  # days: an item tested right once is half-forgotten after a week; each success doubles it


def short(slug: str, name: str | None = None) -> str:
    """Folder name of a course in the vault (course.yaml `short`)."""
    return name or slug.upper()


def frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            try:
                return yaml.safe_load(text[4:end]) or {}, text[end + 4:].lstrip("\n")
            except yaml.YAMLError:
                return {}, text
    return {}, text


def _date(v) -> dt.date | None:
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


@dataclass
class Concept:
    name: str
    courses: list[str]
    topic: str | None
    mastery: float
    evidence: int
    last_tested: dt.date | None
    next_review: dt.date | None
    streak: int
    status: str

    def effective(self, today: dt.date) -> float:
        """Mastery discounted by forgetting since the last test (longer memory after each success)."""
        if self.last_tested is None:
            return self.mastery
        days = max(0, (today - self.last_tested).days)
        half_life = BASE_HALF_LIFE * (2 ** max(0, self.streak))
        return self.mastery * (0.5 ** (days / half_life)) ** 0.5  # sqrt: forgetting a little ≠ losing the points


def load_concepts(vault: Path) -> list[Concept]:
    out = []
    for p in sorted((vault / "Concepts").glob("*.md")):
        fm, _ = frontmatter(p.read_text())
        courses = fm.get("course") or fm.get("courses") or []
        courses = [courses] if isinstance(courses, str) else list(courses)
        out.append(Concept(p.stem, courses, fm.get("topic"), float(fm.get("mastery") or 0), int(fm.get("evidence") or 0),
                           _date(fm.get("last_tested")), _date(fm.get("next_review")), int(fm.get("streak") or 0),
                           str(fm.get("status") or "seen")))
    return out


def load_exam_model(vault: Path, slug: str, short_name: str | None = None) -> dict:
    p = vault / "Courses" / short(slug, short_name) / "Exam model.md"
    if not p.exists():
        return {}
    fm, _ = frontmatter(p.read_text())
    return fm


@dataclass
class TopicState:
    name: str
    weight: float           # share of exam points (0..1)
    mastery: float          # evidence-based, after forgetting
    concepts: int
    tested: int
    due: int
    styles: list[str] = field(default_factory=list)
    weeks: list[int] = field(default_factory=list)

    def taught_by(self, week: int | None) -> bool:
        return week is None or not self.weeks or min(self.weeks) <= week

    @property
    def expected(self) -> float:
        return self.weight * self.mastery

    @property
    def gap(self) -> float:
        return self.weight * (1 - self.mastery)


@dataclass
class Readiness:
    course: str
    date: dt.date
    topics: list[TopicState]
    exam_date: dt.date | None
    confidence: str          # how much to trust the exam model: high (past exams) / medium / low
    unmapped: list[str]      # concepts whose topic isn't in the exam model
    week: int | None = None  # current semester week: topics lectured later aren't suggested yet
    short: str | None = None  # vault folder name of the course

    @property
    def expected_share(self) -> float:
        return sum(t.expected for t in self.topics)

    @property
    def tested_share(self) -> float:
        """Share of exam points backed by at least one test: low = the estimate is mostly a guess."""
        return sum(t.weight for t in self.topics if t.tested)

    def next_actions(self, n: int = 3) -> list[TopicState]:
        """Already-lectured topics ranked by points to gain: weight × what you'd still miss (+ due reviews)."""
        taught = [t for t in self.topics if t.taught_by(self.week)] or self.topics
        return sorted(taught, key=lambda t: (-(t.gap + 0.02 * t.due), t.name))[:n]

    def later(self) -> list[TopicState]:
        return [t for t in self.topics if not t.taught_by(self.week)]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def readiness(vault: Path, slug: str, exam_date: dt.date | None, today: dt.date | None = None,
              semester_start: dt.date | None = None, short_name: str | None = None) -> Readiness | None:
    today = today or dt.date.today()
    model = load_exam_model(vault, slug, short_name)
    topics = model.get("topics") or []
    if not topics:
        return None
    total = sum(float(t.get("weight") or 0) for t in topics) or 1.0
    concepts = [c for c in load_concepts(vault) if slug in c.courses]
    by_topic: dict[str, list[Concept]] = {}
    known = {_norm(t["name"]) for t in topics}
    unmapped = []
    for c in concepts:
        key = _norm(c.topic or "")
        if key in known:
            by_topic.setdefault(key, []).append(c)
        else:
            unmapped.append(c.name)
    states = []
    for t in topics:
        cs = by_topic.get(_norm(t["name"]), [])
        tested = [c for c in cs if c.evidence > 0]
        expected_n = max(len(cs), int(t.get("concepts") or 0), 1)  # untouched concepts of a topic count as 0
        m = sum(c.effective(today) for c in tested) / expected_n if tested else 0.0
        due = sum(1 for c in cs if c.next_review and c.next_review <= today)
        weeks = [int(w) for w in (t.get("weeks") or []) if str(w).isdigit()]
        states.append(TopicState(t["name"], float(t.get("weight") or 0) / total, min(1.0, m), len(cs), len(tested), due,
                                 list(t.get("styles") or []), weeks))
    week = (today - semester_start).days // 7 + 1 if semester_start else None
    return Readiness(slug, today, states, exam_date, str(model.get("confidence") or "low"), unmapped, week, short_name)


def swiss_grade(share: float) -> float:
    """ETH's usual linear scale (6 at 100%, 4 at about 60%); real exams are often curved: a rough guide only."""
    return round(max(1.0, min(6.0, 1 + 5 * share)) * 4) / 4


def render(r: Readiness) -> str:
    days = (r.exam_date - r.date).days if r.exam_date else None
    lines = [f"Expected score now: **{r.expected_share:.0%}** of the points (≈ grade {swiss_grade(r.expected_share):.2f} "
             f"on a linear scale). Backed by tests: {r.tested_share:.0%} of the exam's points.",
             f"Exam: {r.exam_date.isoformat() + f' (in {days} days)' if r.exam_date else 'date not set'} · "
             f"exam model confidence: {r.confidence}", "",
             "| Topic | Share of points | Mastery | Expected | Concepts (tested) | Due |", "|---|---|---|---|---|---|"]
    for t in sorted(r.topics, key=lambda t: -t.weight):
        lines.append(f"| {t.name} | {t.weight:.0%} | {t.mastery:.0%} | {t.expected:.1%} | {t.concepts} ({t.tested}) | {t.due or ''} |")
    lines += ["", "**Next best actions** (most points to gain):"]
    for i, t in enumerate(r.next_actions(), 1):
        why = "not studied yet" if not t.concepts else ("never tested" if not t.tested else f"mastery {t.mastery:.0%}")
        lines.append(f"{i}. **{t.name}**: up to {t.gap:.0%} of the points; {why}"
                     + (f"; typical questions: {', '.join(t.styles)}" if t.styles else ""))
    if r.later():
        lines += ["", f"Not lectured yet (week {r.week}): " + ", ".join(f"{t.name} ({t.weight:.0%})" for t in r.later())]
    if r.unmapped:
        lines += ["", f"Concepts not mapped to an exam topic: {', '.join(r.unmapped)}"]
    return "\n".join(lines)


BEGIN, END = "<!-- readiness:begin (generated by `study readiness`) -->", "<!-- readiness:end -->"


def write_readiness(vault: Path, r: Readiness) -> Path:
    """Update the generated part of Readiness.md and append today's line to its history table."""
    p = vault / "Courses" / short(r.course, r.short) / "Readiness.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = p.read_text() if p.exists() else (
        f"---\ncourse: {r.course}\ntags: [readiness]\n---\n# {short(r.course, r.short)} readiness\n\n{BEGIN}\n{END}\n\n"
        "## History\n| Date | Expected | Backed by tests |\n|---|---|---|\n")
    text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: f"{BEGIN}\n{render(r)}\n{END}", text, flags=re.S)
    row = f"| {r.date.isoformat()} | {r.expected_share:.0%} | {r.tested_share:.0%} |"
    lines = text.rstrip("\n").split("\n")
    if lines and lines[-1].startswith(f"| {r.date.isoformat()} |"):
        lines[-1] = row  # one line per day
    else:
        lines.append(row)
    p.write_text("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------- scaffolding

def init_vault(vault: Path, courses: dict) -> list[str]:
    """Create the structure (never overwriting existing notes)."""
    made = []

    def write(rel: str, text: str):
        p = vault / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            made.append(rel)

    write(".gitignore", ".obsidian/workspace*.json\n.obsidian/cache\n.trash/\n.DS_Store\n")
    write("Templates/Concept.md", CONCEPT_TEMPLATE)
    write("Home.md", HOME)
    for c in courses.values():
        s, info = c.short, c.raw.get("exam_info") or {}
        write(f"Courses/{s}/{s}.md", (
            f"---\ncourse: {c.slug}\ntags: [course]\n---\n# {c.name}\n\n"
            f"**Exam:** {c.exam_date or ('project' if c.project_only else 'date not set')} · {c.raw.get('exam_format') or ''}\n\n"
            f"**Grading:** {info.get('grading', '')}\n\n**Target:** {info.get('target', 'maximum')} · ECTS {info.get('ects', '?')}\n\n"
            f"- [[Courses/{s}/Exam model|Exam model]] · [[Courses/{s}/Readiness|Readiness]]\n"
            f"- Concepts: see below · Sessions: see below\n\n"
            "```dataview\nTABLE topic, status, mastery, next_review FROM \"Concepts\" "
            f"WHERE contains(course, \"{c.slug}\") SORT mastery ASC\n```\n\n"
            "```dataview\nLIST FROM \"Sessions\" " f"WHERE course = \"{c.slug}\" SORT file.name DESC LIMIT 10\n```\n"))
        if not c.project_only:
            write(f"Courses/{s}/Exam model.md", (
                f"---\ncourse: {c.slug}\nconfidence: low   # high = from several past exams; medium = one exam or clear lecturer hints; low = guessed from the syllabus\n"
                "topics: []        # filled by /exam-model: - {name, weight (points share), concepts (how many), styles: [...], evidence: [...]}\n---\n"
                f"# {s} exam model\n\nNot built yet: run `/exam-model` in `ETH/{c.slug}/Claude`.\n"))
    return made


CONCEPT_TEMPLATE = """---
course: []
topic: ""            # the exam-model topic this counts toward
weeks: []
status: seen         # seen → practised → solid
mastery: 0           # 0..1, only from evidence (your answers), never from reading
evidence: 0          # number of graded answers behind it
last_tested:
next_review:
streak: 0
---
## My understanding

## Key facts

## Misconceptions I had

## Connections
prerequisite::
used-in::

## Review log
"""

HOME = """# Study vault

```dataview
TABLE WITHOUT ID file.link AS Concept, course, topic, round(mastery*100) + "%" AS Mastery, next_review AS "Next review"
FROM "Concepts" WHERE next_review AND next_review <= date(today) SORT next_review ASC
```

## Weakest concepts
```dataview
TABLE WITHOUT ID file.link AS Concept, course, topic, round(mastery*100) + "%" AS Mastery
FROM "Concepts" WHERE evidence > 0 SORT mastery ASC LIMIT 15
```

## Courses
```dataview
LIST FROM "Courses" WHERE contains(tags, "course")
```

## Recent sessions
```dataview
LIST FROM "Sessions" SORT file.name DESC LIMIT 10
```

Install the **Dataview** community plugin for the tables above. Readiness per course: `Courses/<C>/Readiness.md`.
"""
