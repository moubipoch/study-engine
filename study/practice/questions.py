"""Question bank, practice sessions, code tests, attempts.

A question is a directory  courses/<slug>/questions/<id>/
  q.md          YAML front matter + the question statement (markdown, LaTeX)
  solution.md   reference answer, ending with '## Rubric' bullets: '- (3) what earns these points'
  src/          (code questions) starter code given to the student
  ref/          (code questions) reference solution (same file names as src/)
  tests/        (code questions) unit mode: test.cpp including "solution.hpp"; io mode: cases.yaml

Front matter keys: title, topic, lecture, kind, difficulty, points, source, cites, grading
  grading: rubric                       -> Claude grades against the rubric
  grading: {mode: unit}                 -> tests/test.cpp compiled with the answer's src/ (exit 0 = pass)
  grading: {mode: io}                   -> tests/cases.yaml [{stdin, stdout}] against the answer's program
  grading: {mode: unit, profile: tsan}  -> any run-cpp profile (check | tsan | bench | plain)
  grading: {mode: py}                   -> tests/test_*.py (pytest) run against the answer's src/*.py
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from study.cite import check_text
from study.config import Course

KINDS = {"predict-output", "find-bug", "fix-performance", "refactor-with-templates", "implement", "race-and-speedup",
         "explain", "derive", "prove", "compute", "formulate", "mcq", "short"}
DIFFICULTIES = ("easy", "medium", "hard")
CODE_MODES = ("unit", "io", "py")
_RUBRIC = re.compile(r"^\s*[-*]\s*\((\d+(?:\.\d+)?)\)\s*(.+)$", re.M)


class QuestionError(ValueError):
    pass


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise QuestionError("q.md must start with YAML front matter (---)")
    end = text.find("\n---", 4)
    if end == -1:
        raise QuestionError("unterminated front matter in q.md")
    return yaml.safe_load(text[4:end]) or {}, text[end + 4:].strip()


def _grading(meta: dict) -> dict:
    g = meta.get("grading", "rubric")
    g = {"mode": g} if isinstance(g, str) else dict(g)
    if g.get("mode") not in ("rubric", *CODE_MODES):
        raise QuestionError(f"grading mode must be rubric, unit, io or py; got {g.get('mode')!r}")
    return g


def rubric(solution_md: str) -> list[tuple[float, str]]:
    part = solution_md.split("## Rubric", 1)
    return [(float(p), t.strip()) for p, t in _RUBRIC.findall(part[1])] if len(part) == 2 else []


def dir_hash(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(d)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


@dataclass
class Question:
    id: str
    dir: Path
    meta: dict
    body: str
    solution: str
    grading: dict

    @property
    def is_code(self) -> bool:
        return self.grading["mode"] in CODE_MODES


def load(d: Path) -> Question:
    if not (d / "q.md").is_file():
        raise QuestionError(f"{d}: missing q.md")
    meta, body = _split_frontmatter((d / "q.md").read_text())
    sol = (d / "solution.md").read_text() if (d / "solution.md").is_file() else ""
    return Question(meta.get("id") or d.name, d, meta, body, sol, _grading(meta))


def validate(con: sqlite3.Connection, course: Course, q: Question, build_root: Path) -> list[str]:
    """Raise QuestionError on hard problems; return warnings. Code questions are compiled and tested."""
    errors, warnings = [], []
    m = q.meta
    for k in ("title", "kind", "difficulty", "points"):
        if m.get(k) in (None, ""):
            errors.append(f"missing '{k}'")
    if m.get("kind") and m["kind"] not in KINDS:
        errors.append(f"kind must be one of {sorted(KINDS)}")
    if m.get("difficulty") and m["difficulty"] not in DIFFICULTIES:
        errors.append(f"difficulty must be one of {DIFFICULTIES}")
    if not q.body:
        errors.append("q.md has no question text")
    if not q.solution:
        errors.append("missing solution.md")
    rub = rubric(q.solution)
    if not rub:
        errors.append("solution.md needs a '## Rubric' section with bullets like '- (3) states the invariant'")
    elif m.get("points") is not None and abs(sum(p for p, _ in rub) - float(m["points"])) > 1e-6:
        errors.append(f"rubric points sum to {sum(p for p, _ in rub):g}, but points: {m['points']}")
    cites = " ".join(m.get("cites") or []) + "\n" + q.body + "\n" + q.solution
    _, cite_errors = check_text(con, cites, course.slug)
    errors += [f"bad citation {e.cite}: {e.reason}" for e in cite_errors]
    if not m.get("cites"):
        warnings.append("no 'cites': link the question to where the material covers it")
    if q.is_code and not errors:
        errors += _validate_code(q, build_root)
    if errors:
        raise QuestionError(f"{q.id}: question rejected:\n  - " + "\n  - ".join(errors))
    return warnings


def _validate_code(q: Question, build_root: Path) -> list[str]:
    errs = []
    for sub in ("src", "ref", "tests"):
        if not (q.dir / sub).is_dir():
            errs.append(f"code question needs a {sub}/ directory")
    if errs:
        return errs
    ref = run_tests(q, q.dir / "ref", build_root)
    if not ref["passed"]:
        errs.append(f"reference solution does not pass its own tests: {ref['summary']}")
    if q.grading["mode"] != "py" and ref.get("warnings"):
        errs.append(f"reference solution compiles with {ref['warnings']} warning(s); exercises must be -Wall -Wextra clean")
    if q.meta.get("kind") in ("find-bug", "race-and-speedup", "implement"):
        starter = run_tests(q, q.dir / "src", build_root)
        if starter["passed"]:
            errs.append("starter code already passes the tests, so there is nothing to find/implement")
    return errs


# ---------------------------------------------------------------- tests

def run_tests(q: Question, code_dir: Path, build_root: Path) -> dict:
    """Compile the answer (code_dir) with the question's tests; returns {passed, summary, ...runner fields}."""
    from study.practice.runner_cpp import compile_cpp, run_binary
    g = q.grading
    if g["mode"] == "py":
        from study.practice.runner_py import run_pytest
        work = build_root / f"py-{q.id}-{dir_hash(code_dir)}"
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(code_dir, work)
        for t in (q.dir / "tests").glob("*.py"):
            shutil.copy2(t, work / t.name)
        return run_pytest(work, float(g.get("timeout", 120)))
    profile = g.get("profile", "check")
    timeout = float(g.get("timeout", 10))
    flags = list(g.get("flags", []))
    build_root.mkdir(parents=True, exist_ok=True)
    with_tmp = build_root / f"src-{q.id}-{dir_hash(code_dir)}"
    if with_tmp.exists():
        shutil.rmtree(with_tmp)
    shutil.copytree(code_dir, with_tmp)
    if g["mode"] == "unit":
        shutil.copy2(q.dir / "tests" / "test.cpp", with_tmp / "zz_test.cpp")
        # the answer must not bring its own main() in unit mode
        for f in with_tmp.glob("*.cpp"):
            if f.name != "zz_test.cpp" and re.search(r"\bint\s+main\s*\(", f.read_text()):
                f.rename(f.with_suffix(".cpp.main-ignored"))
        res = compile_cpp(with_tmp, build_root, profile, flags)
        res = run_binary(res, timeout=timeout)
        passed = res.compiled and not res.timed_out and res.exit_code == 0 and not res.sanitizer
        out = res.to_dict() | {"passed": passed}
    else:
        cases = yaml.safe_load((q.dir / "tests" / "cases.yaml").read_text()) or []
        res = compile_cpp(with_tmp, build_root, profile, flags)
        results = []
        for i, c in enumerate(cases if res.compiled else []):
            r = run_binary(dataclasses.replace(res, runs=[]), stdin=str(c.get("stdin", "")), timeout=timeout)
            ok = (r.compiled and not r.timed_out and r.exit_code == 0 and not r.sanitizer
                  and r.stdout.strip() == str(c.get("stdout", "")).strip())
            results.append({"case": i, "ok": ok, "got": r.stdout[-500:], "want": str(c.get("stdout", ""))[-500:],
                            "sanitizer": r.sanitizer, "exit_code": r.exit_code, "timed_out": r.timed_out})
        passed = res.compiled and bool(results) and all(r["ok"] for r in results)
        out = res.to_dict() | {"passed": passed, "cases": results}
    if not out.get("compiled"):
        out["summary"] = "does not compile"
    elif g["mode"] == "io":
        out["summary"] = f"{sum(c['ok'] for c in out['cases'])}/{len(out['cases'])} cases pass"
    else:
        out["summary"] = ("all tests pass" if out["passed"] else
                          "timed out" if out.get("timed_out") else
                          f"sanitizer: {out['sanitizer']}" if out.get("sanitizer") else
                          f"tests fail (exit {out.get('exit_code')})")
    return out


# ---------------------------------------------------------------- bank

def question_id(course: str, meta: dict) -> str:
    lec = meta.get("lecture")
    base = re.sub(r"[^a-z0-9]+", "-", str(meta.get("title", "q")).lower()).strip("-")[:40]
    h = hashlib.sha1(json.dumps(meta, sort_keys=True, default=str).encode()).hexdigest()[:4]
    return f"{course}-w{lec if lec is not None else 'x'}-{base}-{h}"


def add(con: sqlite3.Connection, course: Course, src_dir: Path, build_root: Path) -> tuple[str, list[str]]:
    """Validate a drafted question directory and file it under courses/<slug>/questions/<id>/."""
    q = load(src_dir)
    warnings = validate(con, course, q, build_root)
    qid = q.meta.get("id") or question_id(course.slug, q.meta)
    dest = course.dir / "questions" / qid
    if dest.resolve() != src_dir.resolve():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest)
    m = q.meta
    with con:
        con.execute(
            "INSERT OR REPLACE INTO questions(id,course,title,topic,lecture,kind,difficulty,points,source,grading,dir,content_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (qid, course.slug, m["title"], m.get("topic", ""), m.get("lecture"), m["kind"], m["difficulty"],
             float(m["points"]), m.get("source", "generated"), q.grading["mode"], str(dest), dir_hash(dest)))
    return qid, warnings


def get(con: sqlite3.Connection, qid: str) -> Question:
    row = con.execute("SELECT dir FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        raise QuestionError(f"unknown question {qid}")
    return load(Path(row["dir"]))


def listing(con: sqlite3.Connection, course: str, topic: str | None = None, lecture: int | None = None,
            kind: str | None = None) -> list[dict]:
    q = ("SELECT q.*, (SELECT count(*) FROM attempts a WHERE a.question_id=q.id) AS n_attempts,"
         " (SELECT score FROM attempts a WHERE a.question_id=q.id ORDER BY ts DESC, id DESC LIMIT 1) AS last_score,"
         " (SELECT max(ts) FROM attempts a WHERE a.question_id=q.id) AS last_ts FROM questions q WHERE course=?")
    args: list = [course]
    if topic:
        q, args = q + " AND topic LIKE ?", args + [f"%{topic}%"]
    if lecture is not None:
        q, args = q + " AND lecture=?", args + [lecture]
    if kind:
        q, args = q + " AND kind=?", args + [kind]
    return [dict(r) for r in con.execute(q + " ORDER BY lecture, topic, id", args)]


def pick(con: sqlite3.Connection, course: str, n: int = 3, **filters) -> list[dict]:
    """Practice order: questions FSRS says are due (most overdue first), then never attempted,
    then the rest by lowest last score / least recently practised."""
    from study.srs.fsrs_q import due
    rows = listing(con, course, **filters)
    due_at = {d["id"]: d["due"] for d in due(con, course)}
    rows.sort(key=lambda r: (r["id"] not in due_at, due_at.get(r["id"], ""), r["n_attempts"] > 0,
                             r["last_score"] if r["last_score"] is not None else -1, r["last_ts"] or ""))
    return rows[:n]


# ---------------------------------------------------------------- sessions

def new_session(con: sqlite3.Connection, course: Course, qids: list[str]) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    sdir = course.dir / "practice" / stamp
    sdir.mkdir(parents=True)
    lines = [f"# Practice session {stamp} ({course.name})", "",
             "Write each answer below its question. Show your reasoning: grading is step by step.",
             "For code questions edit the files in the question's folder (`<id>/src/`), then you can run",
             "`uv run study run-tests <id> --session " + stamp + "` yourself before submitting.", ""]
    for i, qid in enumerate(qids, 1):
        q = get(con, qid)
        lines += [f"## Q{i} · {q.meta['title']}  <!-- id: {qid} -->",
                  f"*{q.meta['kind']} · {q.meta['difficulty']} · {q.meta['points']} points*", "", q.body, ""]
        if q.is_code:
            shutil.copytree(q.dir / "src", sdir / qid / "src")
            lines += [f"> Code: edit `{qid}/src/` in this session folder. Explain your fix/approach here:", ""]
        lines += ["### Your answer", "", "", ""]
    (sdir / "answer.md").write_text("\n".join(lines))
    (sdir / "session.json").write_text(json.dumps({"course": course.slug, "questions": qids, "created": stamp}, indent=1))
    return sdir


def session_dir(course: Course, session: str) -> Path:
    d = course.dir / "practice" / session
    if not (d / "session.json").is_file():
        raise QuestionError(f"no practice session {session} in {course.slug}")
    return d


def log_attempt(con: sqlite3.Connection, qid: str, session: str, score: float, *, points: float | None = None,
                steps: list | None = None, feedback: str = "", tests: dict | None = None) -> int:
    if not 0.0 <= score <= 1.0:
        raise QuestionError("score must be between 0 and 1")
    row = con.execute("SELECT points FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        raise QuestionError(f"unknown question {qid}")
    if points is not None and not 0 <= points <= row["points"] + 1e-9:
        raise QuestionError(f"points must be between 0 and {row['points']}")
    with con:
        cur = con.execute("INSERT INTO attempts(question_id,session,score,points,max_points,steps,feedback,tests)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (qid, session, score, points, row["points"], json.dumps(steps or []), feedback,
                           json.dumps(tests or {})))
    from study.srs.fsrs_q import review
    review(con, qid, score)
    return cur.lastrowid


# ---------------------------------------------------------------- answers (GUI)

_Q_HEADER = re.compile(r"^## Q\d+ · .*?<!-- id: (\S+) -->\s*$", re.M)


def read_answers(sdir: Path) -> dict[str, str]:
    text = (sdir / "answer.md").read_text()
    parts = _Q_HEADER.split(text)
    out = {}
    for qid, block in zip(parts[1::2], parts[2::2]):
        ans = block.split("### Your answer", 1)
        out[qid] = ans[1].strip() if len(ans) == 2 else ""
    return out


def write_answers(sdir: Path, answers: dict[str, str]) -> None:
    """Replace the text under each question's '### Your answer' heading, keeping everything else."""
    text = (sdir / "answer.md").read_text()
    marks = list(_Q_HEADER.finditer(text))
    out = [text[:marks[0].start()] if marks else text]
    for m, nxt in zip(marks, marks[1:] + [None]):
        section = text[m.start():nxt.start() if nxt else len(text)]
        head = section.split("### Your answer", 1)[0]
        new = answers.get(m.group(1))
        if new is None:  # untouched question: keep as is
            out.append(section)
        else:
            out.append(head + "### Your answer\n\n" + new.strip() + "\n\n")
    (sdir / "answer.md").write_text("".join(out).rstrip() + "\n")


def session_attempts(con: sqlite3.Connection, session: str) -> dict[str, dict]:
    rows = con.execute("SELECT * FROM attempts WHERE session=? ORDER BY ts", (session,)).fetchall()
    return {r["question_id"]: dict(r) for r in rows}
