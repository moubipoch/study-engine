"""Obsidian vault export: course indexes, weekly digests, glossary, key results, questions, cross-course concepts.

Every generated file carries `generated_by: study` in its front matter. Files without that marker (your own
notes) are never touched, and a generated file you edited is left alone (its hash no longer matches).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

import yaml

from study.cite import CITE_RE
from study.config import Course, topic_name

GUI = "http://127.0.0.1:8090"
MARK = "generated_by: study"


_LOOKALIKE = str.maketrans({":": "꞉", "/": "∕", "\\": "∖", "*": "∗", "?": "？", '"': "”", "<": "‹", ">": "›",
                            "|": "∣", "#": "＃", "^": "ˆ", "[": "(", "]": ")"})


def _safe(name: str) -> str:
    """File-name-safe but still readable: `->` stays `-›`, `::` becomes `꞉꞉` (look-alike characters)."""
    return name.translate(_LOOKALIKE).strip()[:120] or "untitled"


def concept_key(term: str) -> str:
    t = re.sub(r"`|\*|\$", "", term).strip()
    t = re.sub(r"\s*\(.*?\)\s*", " ", t)  # drop parenthetical qualifiers
    return " ".join(t.split()).lower()


def cites_to_links(md: str) -> str:
    """[@course:key#loc] -> [key loc](GUI viewer link): opens the exact passage (+ slide image / frame)."""
    def repl(m):
        loc = m.group("loc").replace("t=", "@ ")
        return f"[{m.group('key')} {loc}]({GUI}/cite?c={quote(m.group(0))})"
    return CITE_RE.sub(repl, md)


def _strip_fm(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


class Writer:
    def __init__(self, root: Path):
        self.root = root
        self.state_file = root / ".study-export.json"
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.written = self.skipped = 0

    def write(self, rel: str, meta: dict, body: str) -> None:
        p = self.root / rel
        fm = "---\n" + yaml.safe_dump(meta | {"generated_by": "study"}, sort_keys=False, allow_unicode=True) + "---\n\n"
        content = fm + body.strip() + "\n"
        if p.exists():
            current = p.read_text()
            if MARK not in current:
                self.skipped += 1  # your own note with the same name: never overwrite
                return
            prev = self.state.get(rel)
            if prev and hashlib.sha1(current.encode()).hexdigest() != prev:
                self.skipped += 1  # generated, then edited by you: keep your edits
                return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self.state[rel] = hashlib.sha1(content.encode()).hexdigest()
        self.written += 1

    def close(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=1))


def export(con: sqlite3.Connection, courses: dict[str, Course], vault: Path) -> dict:
    vault.mkdir(parents=True, exist_ok=True)
    w = Writer(vault)
    concepts: dict[str, dict] = {}  # key -> {"name", "entries": [(course_name, week, definition)]}
    for c in courses.values():
        folder = _safe(c.name)
        weeks = []
        for path in sorted((c.dir / "digests").glob("L*.md")):
            n = int(path.stem[1:])
            text = path.read_text()
            body = _strip_fm(text)
            title = (re.search(r"^# (.+)$", body, re.M) or [None, f"Week {n}"])[1]
            terms = [t for t, in con.execute("SELECT term FROM glossary WHERE course=? AND lecture=? ORDER BY term",
                                              (c.slug, n))]
            for t, d in con.execute("SELECT term, definition FROM glossary WHERE course=? AND lecture=?", (c.slug, n)):
                k = concept_key(t)
                concepts.setdefault(k, {"name": _safe(re.sub(r"`|\*", "", t)), "entries": []})["entries"].append(
                    (c.name, n, d, f"{folder}/Week {n:02d}"))
            footer = "\n\n## Concepts\n" + " · ".join(f"[[{concepts[concept_key(t)]['name']}]]" for t in terms) if terms else ""
            note = f"[[{folder}/_{folder}|{c.name}]] · week {n}\n\n" + cites_to_links(body) + footer
            w.write(f"{folder}/Week {n:02d}.md", {"course": c.slug, "week": n, "title": title,
                                                    "tags": [f"course/{c.slug}", "digest"]}, note)
            weeks.append((n, title))
        for name in ("glossary", "key_results"):
            p = c.dir / f"{name}.md"
            if p.exists():
                body = re.sub(r"\]\(digests/L(\d+)\.md\)", lambda m: f"](Week%20{int(m.group(1)):02d}.md)", p.read_text())
                w.write(f"{folder}/{name.replace('_', ' ').title()}.md", {"course": c.slug, "tags": [f"course/{c.slug}"]},
                        cites_to_links(body))
        qrows = con.execute("SELECT id, title, lecture, kind, difficulty, points, dir FROM questions WHERE course=?"
                            " ORDER BY lecture, id", (c.slug,)).fetchall()
        for q in qrows:
            qdir = Path(q["dir"])
            stmt = _strip_fm((qdir / "q.md").read_text()) if (qdir / "q.md").exists() else ""
            sol = (qdir / "solution.md").read_text() if (qdir / "solution.md").exists() else ""
            folded = "\n".join("> " + ln for ln in sol.splitlines())
            body = (f"*{q['kind']} · {q['difficulty']} · {q['points']:g} points*\n\n{cites_to_links(stmt)}\n\n"
                    f"> [!success]- Solution (try first)\n{cites_to_links(folded)}\n")
            w.write(f"{folder}/Questions/{_safe(q['title'])}.md",
                    {"course": c.slug, "week": q["lecture"], "kind": q["kind"], "id": q["id"],
                     "tags": [f"course/{c.slug}", "question"]}, body)
        profile = c.dir / "exam_profile.yaml"
        idx = [f"# {c.name}", "", f"Exam: {c.exam_date or ('project only' if c.project_only else 'date not set')} · "
               f"priority {c.priority}", ""]
        if c.topics:
            idx += ["## Topics", *[f"- {topic_name(t)}" for t in c.topics], ""]
        if weeks:
            idx += ["## Weeks", *[f"- [[{folder}/Week {n:02d}|Week {n}]]: {t}" for n, t in weeks], ""]
        idx += ["## Reference", f"- [[{folder}/Glossary|Glossary]] · [[{folder}/Key Results|Key results]]"]
        if qrows:
            idx.append(f"- {len(qrows)} practice questions in `{folder}/Questions/`")
        if profile.exists():
            idx += ["", "## Exam profile", "```yaml", profile.read_text().strip(), "```"]
        w.write(f"{folder}/_{folder}.md", {"course": c.slug, "tags": [f"course/{c.slug}", "index"]}, "\n".join(idx))
    for k, info in concepts.items():
        courses_in = sorted({e[0] for e in info["entries"]})
        lines = [f"# {info['name']}", ""]
        if len(courses_in) > 1:
            lines += [f"Appears in **{len(courses_in)} courses**: " + ", ".join(courses_in), ""]
        for course_name, week, definition, link in info["entries"]:
            lines.append(f"- **{course_name}**, [[{link}|week {week}]]: {cites_to_links(definition)}")
        w.write(f"Concepts/{info['name']}.md", {"tags": ["concept"] + (["cross-course"] if len(courses_in) > 1 else [])},
                "\n".join(lines))
    home = ["# Study vault", "", *[f"- [[{_safe(c.name)}/_{_safe(c.name)}|{c.name}]]" for c in courses.values()], "",
            f"Concepts: {len(concepts)} (cross-course: {sum(1 for v in concepts.values() if len({e[0] for e in v['entries']}) > 1)})",
            "", "Citation links open the exact source passage in the study GUI (`uv run study ui`)."]
    w.write("Library.md", {"tags": ["index"]}, "\n".join(home))
    w.close()
    return {"vault": str(vault), "written": w.written, "kept_your_edits": w.skipped, "concepts": len(concepts)}
