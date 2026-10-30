"""`study` CLI: deterministic tools only. Claude Code (slash commands) is the reasoning layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from study import config

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output (for slash commands).")]


def emit(obj, as_json: bool, human=None) -> None:
    if as_json:
        json.dump(obj, sys.stdout, indent=2, default=str, ensure_ascii=False)
        sys.stdout.write("\n")
    elif human:
        human(obj)
    else:
        typer.echo(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def fail(msg: str, code: int = 1):
    typer.secho(msg, fg="red", err=True)
    raise typer.Exit(code)


def paths() -> config.Paths:
    try:
        return config.default_paths()
    except config.ConfigError as e:
        fail(str(e))


def course_or_fail(slug: str) -> config.Course:
    try:
        return config.get_course(paths(), slug)
    except config.ConfigError as e:
        fail(str(e))


@app.command()
def courses(as_json: JsonOpt = False, all: bool = typer.Option(False, "--all", help="Include inactive.")):
    """List configured courses."""
    try:
        cs = config.load_courses(paths(), include_inactive=all)
    except config.ConfigError as e:
        fail(str(e))
    rows = [{
        "slug": c.slug, "name": c.name, "code": c.code, "active": c.active, "priority": c.priority,
        "exam": "project-only" if c.project_only else (c.exam_date.isoformat() if c.exam_date else None),
        "topics": len(c.topics), "topics_proposed": len(c.topics_proposed),
    } for c in sorted(cs.values(), key=lambda c: -c.priority)]

    def human(rows):
        for r in rows:
            exam = r["exam"] or "exam date NOT SET"
            flag = "" if r["active"] else " (inactive)"
            typer.echo(f"{r['slug']:<10} p{r['priority']}  {exam:<18} topics:{r['topics']:>3}  {r['name']}{flag}")
    emit(rows, as_json, human)


@app.command()
def tools(as_json: JsonOpt = False):
    """Check installed compilers, GPU, MPI, Python extras and AnkiConnect."""
    from study.tools import check_all
    res = check_all()

    def human(res):
        for name, r in res.items():
            mark = typer.style("ok ", fg="green") if r["ok"] else typer.style("-- ", fg="yellow")
            typer.echo(f"{mark} {name:<24} {r['detail']}")
    emit(res, as_json, human)


def db():
    from study.db import connect
    return connect(paths().db)


@app.command()
def ingest(
    course: str,
    target: Annotated[str, typer.Argument(help="File, directory, or YouTube URL.")],
    type: Annotated[str | None, typer.Option("--type", "-t", help="recording|slides|script|exercise|exam|note|code")] = None,
    lecture: Annotated[int | None, typer.Option("--lecture", "-l", help="Lecture number (else parsed from name).")] = None,
    key: Annotated[str | None, typer.Option(help="Citation key (file) or key prefix (directory, e.g. mine).")] = None,
    force: Annotated[bool, typer.Option(help="Re-extract even if unchanged / duplicate.")] = False,
    no_copy: Annotated[bool, typer.Option("--no-copy", help="Don't copy into courses/<slug>/materials.")] = False,
    model: Annotated[str | None, typer.Option(help="Whisper model (default large-v3-turbo GPU / medium CPU).")] = None,
    device: Annotated[str, typer.Option(help="auto|cuda|cpu")] = "auto",
    language: Annotated[str | None, typer.Option(help="Audio language, e.g. en/de (default: autodetect).")] = None,
    no_frames: Annotated[bool, typer.Option("--no-frames", help="Skip video keyframe extraction.")] = False,
    no_embed: Annotated[bool, typer.Option("--no-embed", help="Skip computing search embeddings now.")] = False,
    as_json: JsonOpt = False,
):
    """Ingest material into a course (idempotent: unchanged files are skipped)."""
    from study.ingest.pipeline import TYPES, ingest as do_ingest, result_dict
    c = course_or_fail(course)
    if type and type not in TYPES:
        fail(f"--type must be one of {', '.join(TYPES)}")
    opts = {"model": model, "device": device, "language": language, "frames": not no_frames}
    try:
        res = do_ingest(db(), paths(), c, target, typ=type, lecture=lecture, key=key, force=force,
                        copy=not no_copy, opts=opts)
    except (FileNotFoundError, ValueError) as e:
        fail(str(e))

    def human(rs):
        colors = {"added": "green", "updated": "cyan", "unchanged": None, "duplicate": "yellow", "error": "red"}
        for r in rs:
            line = f"{r['status']:<9} {r['type']:<9} {r['key']}"
            if r["chunks"] or r["images"]:
                line += f"  ({r['chunks']} chunks, {r['images']} images)"
            if r["detail"]:
                line += f"  {r['detail']}"
            typer.secho(line, fg=colors.get(r["status"]))
    if not no_embed and any(r.status in ("added", "updated") for r in res):
        _embed(c.slug)
    emit([result_dict(r) for r in res], as_json, human)
    if any(r.status == "error" for r in res):
        raise typer.Exit(1)


def _embed(course: str | None) -> int:
    from study.retrieve.embed import embed_missing, get_embedder

    def progress(done, total):
        print(f"\r[embed] {done}/{total}", end="" if done < total else "\n", file=sys.stderr, flush=True)
    try:
        emb = get_embedder(str(paths().cache / "models"))
        return embed_missing(db(), emb, course, progress=progress)
    except Exception as e:  # offline first run etc.: search falls back to lexical
        typer.secho(f"[embed] skipped: {type(e).__name__}: {e}", fg="yellow", err=True)
        return 0


@app.command()
def embed(course: Annotated[str | None, typer.Argument(help="Course (default: all).")] = None):
    """Compute missing search embeddings (normally done automatically after ingest)."""
    if course:
        course_or_fail(course)
    n = _embed(course)
    typer.echo(f"embedded {n} chunks")


@app.command()
def search(
    course: str,
    query: str,
    k: Annotated[int, typer.Option("-k", help="Number of results.")] = 8,
    lecture: Annotated[int | None, typer.Option("--lecture", "-l")] = None,
    type: Annotated[list[str] | None, typer.Option("--type", "-t", help="Restrict to source types (repeatable).")] = None,
    key: Annotated[str | None, typer.Option(help="Restrict to keys with this prefix (e.g. ex02/).")] = None,
    mode: Annotated[str, typer.Option(help="hybrid|lexical|semantic")] = "hybrid",
    full: Annotated[bool, typer.Option("--full", help="Don't truncate chunk text.")] = False,
    as_json: JsonOpt = False,
):
    """Hybrid (BM25 + embeddings) search; every result carries a ready-to-paste citation."""
    from study.retrieve.embed import get_embedder
    from study.retrieve.search import Filters, search as do_search
    course_or_fail(course)
    emb = None
    if mode != "lexical":
        try:
            emb = get_embedder(str(paths().cache / "models"))
        except Exception:
            emb = None
    res = do_search(db(), course, query, k=k, flt=Filters(lecture, list(type or []), key), mode=mode,
                    embedder=emb, max_chars=None if full else 1200)

    def human(res):
        if res["warning"]:
            typer.secho(res["warning"], fg="yellow", err=True)
        for i, r in enumerate(res["results"], 1):
            typer.secho(f"{i}. {r['cite']}  {r['heading'][:70]}", fg="cyan")
            typer.echo("   " + " ".join(r["text"].split())[:300])
    emit(res, as_json, human)


@app.command()
def bundle(
    course: str,
    lecture: Annotated[int | None, typer.Option("--lecture", "-l")] = None,
    key: Annotated[str | None, typer.Option(help="A single source instead of a whole lecture.")] = None,
    type: Annotated[list[str] | None, typer.Option("--type", "-t")] = None,
    max_tokens: Annotated[int, typer.Option(help="Approximate size limit.")] = 30000,
    offset: Annotated[int, typer.Option(help="Continue from a previous bundle's next_offset.")] = 0,
    headings: Annotated[bool, typer.Option("--headings", help="Only citations + slide/section titles (cheap survey).")] = False,
    as_json: JsonOpt = False,
):
    """Everything about one lecture (or source) in reading order, token-bounded, with citations.

    With --headings and no --lecture/--key: a whole-course outline (one line per slide/section title)."""
    from study.retrieve.bundle import bundle as do_bundle
    course_or_fail(course)
    try:
        res = do_bundle(db(), course, lecture=lecture, key=key, types=list(type or []), max_tokens=max_tokens,
                        offset=offset, headings_only=headings)
    except ValueError as e:
        fail(str(e))

    def human(b):
        if headings:
            for ch in b["chunks"]:
                typer.echo(f"{ch['cite']}  {ch['heading']}")
            return
        for s in b["sources"]:
            typer.secho(f"# {s['key']} ({s['type']}, {s['chunks']} chunks)", fg="green")
        for ch in b["chunks"]:
            typer.secho(f"\n{ch['cite']}  {ch['heading']}", fg="cyan")
            typer.echo(ch["text"])
            for p in ch["images"]:
                typer.secho(f"  image: {p}", fg="magenta")
        if b["truncated"]:
            typer.secho(f"\n[truncated: continue with --offset {b['next_offset']}]", fg="yellow")
    emit(res, as_json, human)


@app.command()
def todo(course: str, kind: Annotated[str, typer.Argument(help="digest")] = "digest",
         all: Annotated[bool, typer.Option("--all", help="Include fresh items.")] = False, as_json: JsonOpt = False):
    """What needs (re)generating: missing or stale (inputs changed) artifacts, in week order."""
    from dataclasses import asdict
    from study.digest import todo as digest_todo
    course_or_fail(course)
    if kind != "digest":
        fail("only 'digest' is supported so far")
    items = [asdict(i) for i in digest_todo(db(), course) if all or i.status != "fresh"]

    def human(items):
        if not items:
            typer.echo("nothing to do: every lecture digest is fresh")
        for i in items:
            color = {"missing": "yellow", "stale": "cyan", "fresh": "green"}[i["status"]]
            typer.secho(f"W{i['lecture']:<3} {i['status']:<8} ~{i['approx_tokens']:>6} tokens  {', '.join(i['sources'])}",
                        fg=color)
    emit(items, as_json, human)


@app.command("save-digest")
def save_digest(course: str, lecture: int, file: Annotated[Path, typer.Argument(help="Markdown digest ('-' = stdin).")],
                as_json: JsonOpt = False):
    """Validate (schema + citations) and store a lecture digest written by Claude."""
    from study.digest import DigestError, save
    c = course_or_fail(course)
    text = sys.stdin.read() if str(file) == "-" else file.read_text()
    try:
        path, v = save(db(), c, lecture, text)
    except DigestError as e:
        fail(str(e))
    out = {"path": str(path), "title": v.title, "cites": v.cites, "glossary": len(v.glossary),
           "key_results": len(v.key_results), "exam_questions": v.exam_questions, "warnings": v.warnings}

    def human(o):
        typer.secho(f"saved {o['path']}: {o['cites']} citations, {o['glossary']} glossary terms, "
                    f"{o['key_results']} key results, {o['exam_questions']} exam questions", fg="green")
        for w in o["warnings"]:
            typer.secho(f"  warning: {w}", fg="yellow")
    emit(out, as_json, human)


@app.command("save-brief")
def save_brief(course: str, lecture: int, file: Annotated[Path, typer.Argument(help="Markdown overview ('-' = stdin).")],
               as_json: JsonOpt = False):
    """Validate (citations, Go deeper questions) and store a short lecture overview written by Claude."""
    from study.brief import BriefError, save
    c = course_or_fail(course)
    text = sys.stdin.read() if str(file) == "-" else file.read_text()
    try:
        _, out = save(db(), c, lecture, text)
    except BriefError as e:
        fail(str(e))

    def human(o):
        typer.secho(f"saved {o['path']}: {o['cites']} citations, {o['follow_ups']} follow-up questions", fg="green")
        for w in o["warnings"]:
            typer.secho(f"  warning: {w}", fg="yellow")
    emit(out, as_json, human)


@app.command("briefs")
def briefs(course: str, as_json: JsonOpt = False):
    """Which weeks have a fresh lecture overview (missing | stale | fresh)."""
    from study.brief import status
    course_or_fail(course)
    st = status(db(), course)
    emit({str(k): v for k, v in st.items()}, as_json, lambda o: [typer.echo(f"week {k:>2}: {v}") for k, v in o.items()])


@app.command("run-cpp")
def run_cpp_cmd(
    target: Annotated[Path, typer.Argument(help="A .cpp file or a directory of sources.")],
    profile: Annotated[str, typer.Option(help="check (ASan+UBSan) | tsan | bench (-O3) | plain")] = "check",
    compile_only: Annotated[bool, typer.Option("--compile-only")] = False,
    arg: Annotated[list[str] | None, typer.Option("--arg", help="Program argument (repeatable).")] = None,
    stdin: Annotated[str, typer.Option(help="Text fed to stdin.")] = "",
    flag: Annotated[list[str] | None, typer.Option("--flag", help="Extra compiler flag, e.g. -fopenmp (repeatable).")] = None,
    link: Annotated[list[str] | None, typer.Option("--link", help="Linker arg, e.g. -lm (repeatable).")] = None,
    repeat: Annotated[int, typer.Option(help="Runs (bench: median wall time).")] = 1,
    timeout: Annotated[float, typer.Option(help="Wall-clock seconds per run.")] = 10.0,
    as_json: JsonOpt = False,
):
    """Compile (with -Wall -Wextra + sanitizers by default) and run C++; reports warnings, output, sanitizer hits."""
    from study.practice.runner_cpp import run_cpp
    if not target.exists():
        fail(f"not found: {target}")
    try:
        res = run_cpp(target.resolve(), paths().data / "build", profile=profile, args=arg, stdin=stdin,
                      timeout=timeout, repeat=repeat, extra_flags=flag, links=link, compile_only=compile_only)
    except ValueError as e:
        fail(str(e))
    out = res.to_dict()

    def human(o):
        if not o["compiled"]:
            typer.secho("COMPILE ERROR", fg="red")
            typer.echo(o["compile_output"])
            return
        typer.secho(f"compiled ({o['profile']}), {o['warnings']} warnings", fg="yellow" if o["warnings"] else "green")
        if o["warnings"]:
            typer.echo(o["compile_output"])
        if compile_only:
            return
        if o["timed_out"]:
            typer.secho("TIMED OUT", fg="red")
        typer.echo(o["stdout"], nl=False)
        if o["stderr"]:
            typer.secho(o["stderr"], fg="red", err=True)
        typer.secho(f"exit {o['exit_code']}" + (f"  median {o['seconds']:.4f}s over {len(o['runs'])} runs" if o["seconds"] else "")
                    + (f"  SANITIZER: {o['sanitizer']}" if o["sanitizer"] else ""), fg="cyan")
    emit(out, as_json, human)
    if not res.compiled or res.timed_out or res.sanitizer or (res.exit_code not in (0, None)):
        raise typer.Exit(2)


q_app = typer.Typer(no_args_is_help=True, help="Question bank: add (validated), list, show.")
app.add_typer(q_app, name="q")


@q_app.command("add")
def q_add(course: str, dirs: Annotated[list[Path], typer.Argument(help="Drafted question folder(s).")],
          as_json: JsonOpt = False):
    """Validate (front matter, rubric, citations; code: compile + reference passes, starter fails) and file questions."""
    from study.practice.questions import QuestionError, add
    c = course_or_fail(course)
    out, bad = [], 0
    for d in dirs:
        try:
            qid, warnings = add(db(), c, d.resolve(), paths().data / "build")
            out.append({"dir": str(d), "id": qid, "ok": True, "warnings": warnings})
        except QuestionError as e:
            bad += 1
            out.append({"dir": str(d), "ok": False, "error": str(e)})

    def human(rows):
        for r in rows:
            if r["ok"]:
                typer.secho(f"added {r['id']}", fg="green")
                for w in r["warnings"]:
                    typer.secho(f"  warning: {w}", fg="yellow")
            else:
                typer.secho(r["error"], fg="red")
    emit(out, as_json, human)
    if bad:
        raise typer.Exit(1)


@q_app.command("list")
def q_list(course: str, topic: Annotated[str | None, typer.Option()] = None,
           lecture: Annotated[int | None, typer.Option("--lecture", "-l")] = None,
           kind: Annotated[str | None, typer.Option()] = None, as_json: JsonOpt = False):
    """Questions with attempt counts and last score."""
    from study.practice.questions import listing
    course_or_fail(course)
    rows = listing(db(), course, topic, lecture, kind)

    def human(rows):
        for r in rows:
            last = f"{r['last_score']:.0%}" if r["last_score"] is not None else "new"
            typer.echo(f"{r['id']:<55} W{r['lecture'] if r['lecture'] is not None else '-':<3} {r['kind']:<16} "
                       f"{r['difficulty']:<7} {r['n_attempts']}x {last}")
    emit(rows, as_json, human)


@q_app.command("show")
def q_show(qid: str, solution: Annotated[bool, typer.Option("--solution")] = False, as_json: JsonOpt = False):
    """Print a question (and, with --solution, its reference answer and rubric)."""
    from study.practice.questions import QuestionError, get, rubric
    try:
        q = get(db(), qid)
    except QuestionError as e:
        fail(str(e))
    out = {"id": qid, "meta": q.meta, "body": q.body, "dir": str(q.dir), "grading": q.grading}
    if solution:
        out |= {"solution": q.solution, "rubric": rubric(q.solution)}
    emit(out, as_json, lambda o: typer.echo(f"# {o['meta']['title']}\n\n{o['body']}" +
                                           (f"\n\n---\n{o['solution']}" if solution else "")))


@app.command()
def practice(course: str, n: Annotated[int, typer.Option("-n")] = 3,
             topic: Annotated[str | None, typer.Option()] = None,
             lecture: Annotated[int | None, typer.Option("--lecture", "-l")] = None,
             kind: Annotated[str | None, typer.Option()] = None,
             ids: Annotated[list[str] | None, typer.Option("--id", help="Specific question ids (repeatable).")] = None,
             as_json: JsonOpt = False):
    """Start a practice session: picks unseen/weak questions and writes an answer sheet to fill in."""
    from study.practice.questions import new_session, pick
    c = course_or_fail(course)
    qids = list(ids or []) or [r["id"] for r in pick(db(), course, n, topic=topic, lecture=lecture, kind=kind)]
    if not qids:
        fail("no matching questions in the bank yet: generate some with /quiz or /exercise")
    sdir = new_session(db(), c, qids)
    out = {"session": sdir.name, "dir": str(sdir), "answer_file": str(sdir / "answer.md"), "questions": qids}
    emit(out, as_json, lambda o: typer.echo(f"session {o['session']}: answer in {o['answer_file']}\n"
                                            + "\n".join(f"  - {q}" for q in o["questions"])))


@app.command("session")
def session_cmd(course: str, session: str, as_json: JsonOpt = False):
    """A practice session's questions, your answers (split per question) and code folders (for /grade)."""
    import json as _json
    import re as _re
    from study.practice.questions import QuestionError, session_dir
    c = course_or_fail(course)
    try:
        d = session_dir(c, session)
    except QuestionError as e:
        fail(str(e))
    meta = _json.loads((d / "session.json").read_text())
    text = (d / "answer.md").read_text()
    parts = _re.split(r"^## Q\d+ · .*?<!-- id: (\S+) -->\s*$", text, flags=_re.M)
    answers = {}
    for qid, block in zip(parts[1::2], parts[2::2]):
        ans = block.split("### Your answer", 1)
        answers[qid] = ans[1].strip() if len(ans) == 2 else ""
    out = {"session": session, "dir": str(d), "questions": [
        {"id": q, "answer": answers.get(q, ""), "code_dir": str(d / q / "src") if (d / q / "src").is_dir() else None,
         "attempted": con_count(q, session)} for q in meta["questions"]]}
    emit(out, as_json)


def con_count(qid: str, session: str) -> int:
    return db().execute("SELECT count(*) FROM attempts WHERE question_id=? AND session=?", (qid, session)).fetchone()[0]


@app.command("run-tests")
def run_tests_cmd(qid: str, session: Annotated[str | None, typer.Option(help="Test your session copy of the code.")] = None,
                  code: Annotated[Path | None, typer.Option(help="Test this folder instead.")] = None,
                  ref: Annotated[bool, typer.Option("--ref", help="Test the reference solution.")] = False,
                  as_json: JsonOpt = False):
    """Compile + run a code question's tests against an answer (sanitizers on)."""
    from study.practice.questions import QuestionError, get, run_tests
    con = db()
    try:
        q = get(con, qid)
    except QuestionError as e:
        fail(str(e))
    if not q.is_code:
        fail(f"{qid} is not a code question")
    if ref:
        target = q.dir / "ref"
    elif code:
        target = code
    elif session:
        course = con.execute("SELECT course FROM questions WHERE id=?", (qid,)).fetchone()["course"]
        target = config.get_course(paths(), course).dir / "practice" / session / qid / "src"
    else:
        fail("give --session, --code or --ref")
    if not target.is_dir():
        fail(f"not found: {target}")
    res = run_tests(q, target.resolve(), paths().data / "build")

    def human(r):
        typer.secho(("PASS " if r["passed"] else "FAIL ") + r["summary"], fg="green" if r["passed"] else "red")
        if r.get("warnings"):
            typer.secho(r["compile_output"], fg="yellow")
        if not r.get("compiled"):
            typer.echo(r["compile_output"])
        for c in r.get("cases", []):
            if not c["ok"]:
                typer.echo(f"  case {c['case']}: want {c['want']!r} got {c['got']!r}")
        if r.get("stderr"):
            typer.echo(r["stderr"][-3000:])
    emit(res, as_json, human)


@app.command("log-attempt")
def log_attempt_cmd(qid: str, session: str,
                    score: Annotated[float, typer.Option(help="0..1 overall.")],
                    points: Annotated[float | None, typer.Option()] = None,
                    steps: Annotated[Path | None, typer.Option(help="JSON list [{step, points, max, comment}].")] = None,
                    feedback: Annotated[Path | None, typer.Option(help="Markdown feedback file.")] = None,
                    tests: Annotated[Path | None, typer.Option(help="JSON from run-tests.")] = None):
    """Record a graded attempt (used by /grade)."""
    import json as _json
    from study.practice.questions import QuestionError, log_attempt
    try:
        aid = log_attempt(db(), qid, session, score, points=points,
                          steps=_json.loads(steps.read_text()) if steps else None,
                          feedback=str(feedback.resolve()) if feedback else "",
                          tests=_json.loads(tests.read_text()) if tests else None)
    except (QuestionError, ValueError) as e:
        fail(str(e))
    typer.echo(f"logged attempt {aid}: {qid} {score:.0%}")


@app.command("exam-profile")
def exam_profile(course: str, file: Annotated[Path | None, typer.Argument(help="YAML to save (omit to show).")] = None):
    """Show or save courses/<slug>/exam_profile.yaml (format/difficulty calibration from past exams)."""
    import yaml as _yaml
    c = course_or_fail(course)
    p = c.dir / "exam_profile.yaml"
    if file is None:
        typer.echo(p.read_text() if p.exists() else "no exam profile yet: run /parse-exams")
        return
    data = _yaml.safe_load(file.read_text())
    need = {"exams_analyzed", "format", "question_types", "difficulty"}
    if not isinstance(data, dict) or not need <= data.keys():
        fail(f"exam profile needs keys: {sorted(need)}")
    p.write_text(_yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
    typer.echo(f"saved {p}")


cards_app = typer.Typer(no_args_is_help=True, help="Flashcards (local source of truth, mirrored to Anki).")
app.add_typer(cards_app, name="cards")
anki_app = typer.Typer(no_args_is_help=True, help="Anki sync via AnkiConnect, or .apkg export.")
app.add_typer(anki_app, name="anki")


@cards_app.command("add")
def cards_add(course: str, file: Annotated[Path, typer.Argument(help="YAML/JSON list of {front, back, cite, topic, lecture, tags}.")],
              as_json: JsonOpt = False):
    """Add/update cards (each needs a valid citation). Unchanged cards are skipped."""
    import yaml as _yaml
    from study.srs.cards import add_cards
    course_or_fail(course)
    items = _yaml.safe_load(file.read_text()) or []
    res = add_cards(db(), course, items if isinstance(items, list) else items.get("cards", []))

    def human(r):
        typer.secho(f"added {r['added']}, updated {r['updated']}, unchanged {r['unchanged']}", fg="green")
        for e in r["errors"]:
            typer.secho(f"  rejected {e}", fg="red")
    emit(res, as_json, human)
    if res["errors"]:
        raise typer.Exit(1)


@cards_app.command("list")
def cards_list(course: str, as_json: JsonOpt = False):
    """Cards with their Anki state (after `study anki pull`)."""
    from study.srs.cards import list_cards
    course_or_fail(course)
    rows = list_cards(db(), course)

    def human(rows):
        for r in rows:
            st = f"reps {r['reps']} lapses {r['lapses']} ivl {r['interval']}d" if r["reps"] is not None else "not in Anki yet"
            typer.echo(f"{r['id']}  W{r['lecture'] if r['lecture'] is not None else '-':<3} {r['front'][:60]:<60}  {st}")
    emit(rows, as_json, human)


@cards_app.command("edit")
def cards_edit(card: str, front: Annotated[str | None, typer.Option()] = None,
               back: Annotated[str | None, typer.Option()] = None):
    """Change a card's text (pushed to Anki on the next `study anki push`; its review history is kept)."""
    from study.srs.cards import CardError, edit_card
    try:
        edit_card(db(), card, front, back)
    except CardError as e:
        fail(str(e))
    typer.echo(f"edited {card}: run `study anki push` to update Anki")


def _anki():
    from study.srs.anki import AnkiConnect
    return AnkiConnect()


@anki_app.command("push")
def anki_push(course: str, as_json: JsonOpt = False):
    """Add new / update changed cards in Anki (needs Anki open with AnkiConnect)."""
    from study.srs.anki import AnkiError, push
    c = course_or_fail(course)
    try:
        res = push(db(), course, c.anki_deck, _anki())
    except AnkiError as e:
        fail(f"{e}\nNo Anki? Use `study anki export {course}` and import the .apkg instead.")
    emit(res, as_json, lambda r: typer.echo(f"Anki: {r['added']} added, {r['updated']} updated ({r['total']} cards)"))


@anki_app.command("pull")
def anki_pull(course: str, as_json: JsonOpt = False):
    """Fetch review state + history from Anki (feeds mastery and /anki help)."""
    from study.srs.anki import AnkiError, pull
    c = course_or_fail(course)
    try:
        res = pull(db(), course, c.anki_deck, _anki())
    except AnkiError as e:
        fail(str(e))
    emit(res, as_json, lambda r: typer.echo(f"pulled {r['cards']} cards, {r['reviews']} reviews, {r.get('flagged', 0)} flagged"))


@anki_app.command("export")
def anki_export(course: str, out: Annotated[Path | None, typer.Option(help="Output .apkg")] = None):
    """Write an .apkg to import into Anki (re-importing updates cards instead of duplicating)."""
    from study.srs.anki import export_apkg
    c = course_or_fail(course)
    out = out or paths().data / "anki" / f"{course}.apkg"
    n = export_apkg(db(), course, c.anki_deck, out)
    typer.echo(f"wrote {n} cards to {out}  (Anki: File → Import)")


@anki_app.command("struggling")
def anki_struggling(course: str, limit: int = 20, as_json: JsonOpt = False):
    """Flagged cards and leeches (many lapses / short intervals), for /anki."""
    from study.srs.anki import struggling
    course_or_fail(course)
    rows = struggling(db(), course, limit)

    def human(rows):
        if not rows:
            typer.echo("nothing struggling (or not pulled yet: `study anki pull`)")
        for r in rows:
            typer.echo(f"{'⚑ ' if r['flags'] else '  '}{r['id']} lapses {r['lapses']} ivl {r['interval']}d  {r['front'][:70]}")
    emit(rows, as_json, human)


@app.command()
def mastery(course: str, as_json: JsonOpt = False):
    """Per-topic mastery: recency-weighted practice scores + recent Anki recall. Weakest first."""
    from study.srs.mastery import mastery as compute
    c = course_or_fail(course)
    rows = compute(db(), c)

    def pct(x):
        return f"{x:5.0%}" if x is not None else "    -"

    def human(rows):
        typer.echo(f"{'topic':<48} {'mastery':>7} {'practice':>8} {'cards':>6}  evidence")
        for r in rows:
            typer.echo(f"{r['topic'][:48]:<48} {pct(r['mastery']):>7} {pct(r['question_score']):>8} {pct(r['card_recall']):>6}"
                       f"  {r['attempts']} attempts / {r['reviews']} reviews ({r['questions']} q, {r['cards']} cards)")
    emit(rows, as_json, human)


@app.command()
def due(course: str, as_json: JsonOpt = False):
    """Bank questions FSRS says are due for re-practice."""
    from study.srs.fsrs_q import due as due_q
    course_or_fail(course)
    rows = due_q(db(), course)
    emit(rows, as_json, lambda rows: typer.echo("\n".join(f"{r['due'][:16]}  {r['id']}" for r in rows) or "nothing due"))


@app.command()
def plan(days: Annotated[int, typer.Option(help="How many days to plan.")] = 7,
         start: Annotated[str | None, typer.Option(help="YYYY-MM-DD (default: today).")] = None,
         busy: Annotated[Path | None, typer.Option(help="JSON {YYYY-MM-DD: busy minutes} to subtract (e.g. from your calendar).")] = None,
         as_json: JsonOpt = False):
    """Daily study plan from priorities, backlog, mastery and (if set) exam dates; writes data/plan/plan.{md,ics}."""
    import datetime as _dt
    import json as _json
    from study.plan.planner import PlanError, Settings, build_plan, to_ics, to_markdown
    try:
        settings = Settings.load(paths().courses / "planner.yaml")
    except PlanError as e:
        fail(str(e))
    d0 = _dt.date.fromisoformat(start) if start else _dt.date.today()
    res = build_plan(db(), config.load_courses(paths()), settings, d0, days,
                     _json.loads(busy.read_text()) if busy else None)
    out = paths().data / "plan"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.md").write_text(to_markdown(res))
    (out / "plan.ics").write_text(to_ics(res))
    res["files"] = {"markdown": str(out / "plan.md"), "ics": str(out / "plan.ics")}

    def human(r):
        typer.echo(to_markdown(r))
        typer.secho(f"written: {r['files']['markdown']} and {r['files']['ics']} (import the .ics into any calendar)", fg="green")
    emit(res, as_json, human)


@app.command()
def export(vault: Annotated[Path | None, typer.Option(help="Folder (default: <vault>/Library: generated reference notes).")] = None,
           as_json: JsonOpt = False):
    """Export an Obsidian vault: course indexes, weekly digests, glossary, key results, questions, concepts."""
    from study.export.obsidian import export as do_export
    target = (vault or paths().vault / "Library").resolve()
    res = do_export(db(), config.load_courses(paths()), target)
    emit(res, as_json, lambda r: typer.echo(f"vault {r['vault']}: {r['written']} notes written, "
                                            f"{r['kept_your_edits']} kept (your edits), {r['concepts']} concepts"))


viz_app = typer.Typer(no_args_is_help=True, help="Phase portraits and bifurcation diagrams (NLD, control).")
app.add_typer(viz_app, name="viz")


@viz_app.command("phase")
def viz_phase(fx: Annotated[str, typer.Argument(help="x' as an expression in x, y, e.g. 'y'")],
              fy: Annotated[str, typer.Argument(help="y' e.g. '-sin(x) - 0.2*y'")],
              xlim: Annotated[tuple[float, float], typer.Option()] = (-3.0, 3.0),
              ylim: Annotated[tuple[float, float], typer.Option()] = (-3.0, 3.0),
              out: Annotated[Path | None, typer.Option(help="PNG path (default data/tmp/plots/phase.png)")] = None,
              title: str = "", as_json: JsonOpt = False):
    """Phase portrait with nullclines, trajectories, and fixed points classified from the Jacobian."""
    from study.viz import ExprError, phase_portrait
    out = out or paths().data / "tmp" / "plots" / "phase.png"
    try:
        res = phase_portrait(fx, fy, xlim, ylim, out, title)
    except (ExprError, SyntaxError) as e:
        fail(f"bad expression: {e}")

    def human(r):
        for p in r["fixed_points"]:
            typer.echo(f"({p['x']:g}, {p['y']:g})  {p['kind']}   eigenvalues {', '.join(p['eigenvalues'])}")
        typer.secho(f"plot: {r['image']}", fg="green")
    emit(res, as_json, human)


@viz_app.command("bifurcation")
def viz_bif(f: Annotated[str, typer.Argument(help="x' = f(x; r), e.g. 'r*x - x**3'")],
            param: Annotated[str, typer.Option()] = "r",
            prange: Annotated[tuple[float, float], typer.Option()] = (-2.0, 2.0),
            xlim: Annotated[tuple[float, float], typer.Option()] = (-3.0, 3.0),
            out: Annotated[Path | None, typer.Option()] = None, title: str = "", as_json: JsonOpt = False):
    """1-D bifurcation diagram: equilibria vs parameter (stable/unstable) and where their number changes."""
    from study.viz import ExprError, bifurcation_1d
    out = out or paths().data / "tmp" / "plots" / "bifurcation.png"
    try:
        res = bifurcation_1d(f, param, prange, xlim, out, title)
    except (ExprError, SyntaxError) as e:
        fail(f"bad expression: {e}")
    emit(res, as_json, lambda r: typer.echo(f"bifurcations near {param} = {r['bifurcation_points']}\nplot: {r['image']}"))


@app.command("run-py")
def run_py_cmd(script: Path, timeout: float = 60, as_json: JsonOpt = False):
    """Run a Python exercise script (numpy/scipy/matplotlib available, plots saved not shown)."""
    from study.practice.runner_py import run_py
    if not script.is_file():
        fail(f"not found: {script}")
    res = run_py(script.resolve(), timeout)
    emit(res, as_json, lambda r: (typer.echo(r["stdout"], nl=False), r["stderr"] and typer.secho(r["stderr"], fg="red"),
                                  typer.secho("TIMED OUT" if r["timed_out"] else f"exit {r['exit_code']}", fg="cyan")))
    if res["timed_out"] or res["exit_code"]:
        raise typer.Exit(2)


@app.command()
def status(as_json: JsonOpt = False):
    """One-screen overview per course: material, digests, questions, cards, mastery, due, exam date."""
    from study.digest import todo
    from study.srs.fsrs_q import due as due_q
    from study.srs.mastery import mastery as mast
    con = db()
    rows = []
    for c in sorted(config.load_courses(paths()).values(), key=lambda c: -c.priority):
        t = todo(con, c.slug)
        m = [x["mastery"] for x in mast(con, c) if x["mastery"] is not None]
        rows.append({
            "course": c.slug, "exam": "project" if c.project_only else (c.exam_date.isoformat() if c.exam_date else "not set"),
            "sources": con.execute("SELECT count(*) FROM sources WHERE course=?", (c.slug,)).fetchone()[0],
            "rec_hours": round(con.execute("SELECT coalesce(sum(extent),0)/3600.0 FROM sources WHERE course=? AND type='recording'",
                                           (c.slug,)).fetchone()[0], 1),
            "weeks": len(t), "digests_fresh": sum(i.status == "fresh" for i in t),
            "digests_stale": sum(i.status == "stale" for i in t),
            "exams": con.execute("SELECT count(*) FROM sources WHERE course=? AND type='exam'", (c.slug,)).fetchone()[0],
            "questions": con.execute("SELECT count(*) FROM questions WHERE course=?", (c.slug,)).fetchone()[0],
            "cards": con.execute("SELECT count(*) FROM cards WHERE course=?", (c.slug,)).fetchone()[0],
            "due": len(due_q(con, c.slug)), "mastery": round(sum(m) / len(m), 2) if m else None})

    def human(rows):
        typer.echo(f"{'course':<10} {'exam':<11} {'src':>4} {'rec h':>6} {'digests':>9} {'exams':>5} {'q':>4} {'cards':>5} {'due':>4} {'mastery':>7}")
        for r in rows:
            typer.echo(f"{r['course']:<10} {r['exam']:<11} {r['sources']:>4} {r['rec_hours']:>6} "
                       f"{str(r['digests_fresh']) + '/' + str(r['weeks']):>9} {r['exams']:>5} {r['questions']:>4} "
                       f"{r['cards']:>5} {r['due']:>4} {('%d%%' % (r['mastery'] * 100)) if r['mastery'] is not None else '-':>7}")
    emit(rows, as_json, human)


topics_app = typer.Typer(no_args_is_help=True, help="Topic lists: Claude proposes, you approve.")
app.add_typer(topics_app, name="topics")


@topics_app.command("show")
def topics_show(course: str, as_json: JsonOpt = False):
    """Approved and proposed topics."""
    c = course_or_fail(course)
    out = {"topics": c.topics, "topics_proposed": c.topics_proposed}

    def human(o):
        from study.config import topic_name
        typer.secho(f"Approved ({len(o['topics'])}):", fg="green")
        for t in o["topics"]:
            typer.echo(f"  - {topic_name(t)}")
        typer.secho(f"Proposed ({len(o['topics_proposed'])}):", fg="yellow")
        for t in o["topics_proposed"]:
            summary = t.get("summary", "") if isinstance(t, dict) else ""
            typer.echo(f"  ? {topic_name(t)}" + (f"  — {summary}" if summary else ""))
    emit(out, as_json, human)


@topics_app.command("propose")
def topics_propose(course: str, file: Annotated[Path, typer.Argument(help="YAML/JSON list ('-' = stdin).")]):
    """Replace the proposed topic list (citations are checked)."""
    import yaml
    from study.topics import TopicError, propose
    c = course_or_fail(course)
    text = sys.stdin.read() if str(file) == "-" else file.read_text()
    items = yaml.safe_load(text) or []
    if isinstance(items, dict):
        items = items.get("topics_proposed") or items.get("topics") or []
    try:
        topics = propose(db(), c, items)
    except TopicError as e:
        fail(str(e))
    typer.echo(f"proposed {len(topics)} topics for {course}; review with `study topics show {course}` or in the GUI")


@topics_app.command("approve")
def topics_approve(course: str, names: Annotated[list[str] | None, typer.Argument(help="Topic names (default: all).")] = None):
    """Approve proposed topics (all, or the named ones)."""
    from study.topics import TopicError, approve
    c = course_or_fail(course)
    try:
        took, left = approve(c, names or None)
    except TopicError as e:
        fail(str(e))
    typer.echo(f"approved {len(took)}; {len(left)} still proposed")


@topics_app.command("remove")
def topics_remove(course: str, names: list[str]):
    """Remove approved topics."""
    from study.topics import remove
    gone = remove(course_or_fail(course), names)
    typer.echo(f"removed {len(gone)}")


@app.command()
def sources(course: str, lecture: Annotated[int | None, typer.Option("--lecture", "-l")] = None,
            type: Annotated[str | None, typer.Option("--type", "-t")] = None, as_json: JsonOpt = False):
    """List ingested sources of a course."""
    from study.queries import list_sources
    course_or_fail(course)
    rows = list_sources(db(), course, lecture, type)

    def human(rows):
        from study.cite import fmt_time
        for r in rows:
            ext = fmt_time(r["extent"]) if r["loc_kind"] == "t" else f"{int(r['extent'])}{r['loc_kind']}"
            lec = f"L{r['lecture']:02d}" if r["lecture"] is not None else "   "
            flag = "  [needs transcription]" if r["meta"].get("needs_transcription") else ""
            typer.echo(f"{lec} {r['type']:<9} {r['key']:<40} {ext:>9} {r['chunks']:>4}ch {r['images']:>3}img{flag}")
    emit(rows, as_json, human)


@app.command()
def show(citation: Annotated[str, typer.Argument(help="e.g. '[@ml:L03-slides#p12]'")],
         context: Annotated[int, typer.Option(help="Neighbouring chunks to include.")] = 0, as_json: JsonOpt = False):
    """Print the source text behind a citation (and any page image / frame paths)."""
    from study.queries import show_citation
    out = show_citation(db(), citation, context)
    if "error" in out:
        fail(out["error"])

    def human(o):
        for ch in o["chunks"]:
            typer.secho(f"--- {ch['heading']} [{ch['loc_start']:g}–{ch['loc_end']:g}]", fg="cyan")
            typer.echo(ch["text"])
        for p in o["images"]:
            typer.secho(f"image: {p}", fg="magenta")
    emit(out, as_json, human)


@app.command("check-cites")
def check_cites(files: list[Path], course: Annotated[str | None, typer.Option(help="Require this course.")] = None,
                as_json: JsonOpt = False):
    """Verify that every [@course:key#loc] citation in the files resolves to an ingested location."""
    from study.cite import check_text
    con = db()
    report, bad = [], 0
    for f in files:
        cites, errors = check_text(con, f.read_text(), course)
        bad += len(errors)
        report.append({"file": str(f), "cites": len(cites), "errors": [e.__dict__ for e in errors]})

    def human(rep):
        for r in rep:
            mark = typer.style("ok ", fg="green") if not r["errors"] else typer.style("BAD", fg="red")
            typer.echo(f"{mark} {r['file']}: {r['cites']} citations, {len(r['errors'])} invalid")
            for e in r["errors"]:
                typer.echo(f"     {e['cite']}  ->  {e['reason']}")
    emit(report, as_json, human)
    if bad:
        raise typer.Exit(1)


@app.command()
def forget(course: str, keys: list[str]):
    """Remove sources from the index (files on disk are untouched); re-ingest to re-key them."""
    con = db()
    with con:
        for k in keys:
            n = con.execute("DELETE FROM sources WHERE course=? AND key=?", (course, k)).rowcount
            typer.echo(f"{'removed' if n else 'not found'}: {course}:{k}")


@app.command()
def relabel(course: str, key: str,
            new_key: Annotated[str | None, typer.Option("--key", help="New citation key.")] = None,
            lecture: Annotated[int | None, typer.Option("--lecture", "-l", help="Lecture/week number.")] = None,
            no_lecture: Annotated[bool, typer.Option("--no-lecture", help="Clear the lecture number.")] = False,
            type: Annotated[str | None, typer.Option("--type", "-t")] = None):
    """Change a source's key, lecture number or type in place (no re-extraction)."""
    from study.ingest.pipeline import relabel_source
    try:
        relabel_source(db(), course, key, new_key=new_key, lecture=lecture, clear_lecture=no_lecture, typ=type)
    except (KeyError, ValueError) as e:
        fail(str(e))
    typer.echo(f"relabelled {course}:{key}" + (f" -> {new_key}" if new_key else ""))


@app.command("note-text")
def note_text(course: str, key: str, file: Annotated[Path, typer.Argument(help="File with the transcription ('-' = stdin).")]):
    """Store Claude's transcription of an image note so it becomes searchable."""
    from study.ingest.pipeline import set_note_text
    text = sys.stdin.read() if str(file) == "-" else file.read_text()
    try:
        set_note_text(db(), course, key, text)
    except KeyError as e:
        fail(str(e))
    typer.echo(f"stored transcription for {course}:{key}")


teach_app = typer.Typer(no_args_is_help=True, help="Interactive lessons (/teach): plan, position, struggles, checks.")
app.add_typer(teach_app, name="teach")


def _teach_human(s):
    if not s:
        typer.echo("no lesson yet")
        return
    typer.secho(f"{s['course']} {s['target']}: {s['title']}  ({s['position']}/{s['total']}, {s['status']})", fg="green")
    for i, c in enumerate(s["plan"]):
        mark = "x" if i < s["position"] else (">" if i == s["position"] else " ")
        typer.echo(f"  [{mark}] {c['name']}" + (f"  ({c['topic']})" if c.get("topic") else ""))
    for st in s["struggles"]:
        typer.secho(f"  struggled: {st['concept']}: {st['note']}", fg="yellow")


@teach_app.command("show")
def teach_show(course: str, target: Annotated[str, typer.Argument(help="Week (3, W3) or a topic name.")],
               as_json: JsonOpt = False):
    """The saved lesson (plan, position, struggles, check results), or null if none yet."""
    from study.teach import get
    course_or_fail(course)
    emit(get(db(), course, target), as_json, _teach_human)


@teach_app.command("plan")
def teach_plan(course: str, target: str, file: Annotated[Path, typer.Argument(help="YAML/JSON {title, concepts: [{name, topic, cites, why}]} ('-' = stdin).")],
               as_json: JsonOpt = False):
    """Save (or re-plan) a lesson. Every concept needs a valid citation. Progress is kept if the taught part is unchanged."""
    import yaml as _yaml
    from study.teach import TeachError, save_plan
    course_or_fail(course)
    data = _yaml.safe_load(sys.stdin.read() if str(file) == "-" else file.read_text()) or {}
    try:
        s = save_plan(db(), course, target, data.get("title") or target, data.get("concepts") or [])
    except TeachError as e:
        fail(str(e))
    emit(s, as_json, _teach_human)


@teach_app.command("progress")
def teach_progress(course: str, target: str,
                   advance: Annotated[bool, typer.Option("--next", help="Move to the next concept.")] = False,
                   position: Annotated[int | None, typer.Option(help="Jump to concept index (0-based).")] = None,
                   struggle: Annotated[str | None, typer.Option(help="Note what the user struggled with.")] = None,
                   concept: Annotated[str | None, typer.Option(help="Concept the struggle is about (default: current).")] = None,
                   done: Annotated[bool, typer.Option("--done", help="Mark the lesson finished.")] = False,
                   as_json: JsonOpt = False):
    """Update where the user is in a lesson."""
    from study.teach import TeachError, progress
    course_or_fail(course)
    try:
        s = progress(db(), course, target, position=position, advance=advance, struggle=struggle, concept=concept,
                     done=done)
    except TeachError as e:
        fail(str(e))
    emit({k: s[k] for k in ("target", "position", "total", "status")} |
         {"next": s["plan"][s["position"]]["name"] if s["position"] < s["total"] else None}, as_json)


@teach_app.command("check")
def teach_check(course: str, target: str,
                score: Annotated[float, typer.Option(help="0 wrong, 0.5 partly right, 1 right.")],
                concept: Annotated[str | None, typer.Option(help="Default: the current concept.")] = None,
                topic: Annotated[str | None, typer.Option(help="Default: the concept's topic.")] = None,
                as_json: JsonOpt = False):
    """Log a check-question result (it counts toward topic mastery)."""
    from study.teach import TeachError, log_check
    course_or_fail(course)
    try:
        emit(log_check(db(), course, target, score, concept, topic), as_json)
    except TeachError as e:
        fail(str(e))


@teach_app.command("list")
def teach_list(course: Annotated[str | None, typer.Argument()] = None, as_json: JsonOpt = False):
    """Lessons in progress and finished, most recent first."""
    from study.teach import listing
    rows = listing(db(), course)

    def human(rows):
        for r in rows:
            typer.echo(f"{r['course']:<10} {r['target']:<24} {r['position']}/{r['total']} {r['status']:<7} {r['title']}")
    emit(rows, as_json, human)


@app.command()
def pack(course: Annotated[str | None, typer.Argument(help="Course slug (default: all active courses).")] = None,
         as_json: JsonOpt = False):
    """Study pack for a plain chat with Claude: ../<Course>/Claude/ with all material + recording transcripts."""
    from study.pack import build
    cs = [course_or_fail(course)] if course else list(config.load_courses(paths()).values())
    res = {c.slug: build(db(), paths(), c) for c in cs}

    def human(res):
        for slug, r in res.items():
            typer.echo(f"{slug:<10} {r['files']:>4} files, {r['transcripts']:>3} transcripts -> {r['dir']}")
    emit(res, as_json, human)


@app.command("sync")
def sync_cmd(course: Annotated[list[str] | None, typer.Argument(help="Only these courses (default: all).")] = None,
             no_browser: Annotated[bool, typer.Option("--no-browser", help="Only sources that need no ETH login.")] = False,
             no_window: Annotated[bool, typer.Option("--no-window", help="Never open a login window (report instead).")] = False):
    """Fetch everything new for all courses: repos, websites, video.ethz.ch, Moodle, Zoom; transcribe; refresh study folders.

    When your ETH login has expired, a Chrome window opens on the login page; log in and the sync continues."""
    from study.sync_all import run
    rep = run(paths(), browser=not no_browser, interactive=not no_window, only=course or None)
    typer.echo("\n" + rep.markdown())


vault_app = typer.Typer(no_args_is_help=True, help="The knowledge vault (Obsidian + git): concepts, exam models, readiness.")
app.add_typer(vault_app, name="vault")


def _vault_dir() -> Path:
    return paths().vault


@vault_app.command("init")
def vault_init():
    """Create the vault structure (course hubs, exam-model stubs, dashboard); never overwrites notes."""
    from study.vault import init_vault
    made = init_vault(_vault_dir(), config.load_courses(paths()))
    typer.echo(f"{len(made)} files created in {_vault_dir()}" + ("".join(f"\n  {m}" for m in made) if made else ""))


@app.command()
def readiness(course: Annotated[str | None, typer.Argument(help="Course (default: all with an exam model).")] = None,
              write: Annotated[bool, typer.Option("--write", help="Update Courses/<C>/Readiness.md in the vault.")] = False,
              as_json: JsonOpt = False):
    """Expected exam score from your concept nodes × the exam model, per topic, and the next best actions."""
    from study.vault import readiness as compute, render, write_readiness
    cs = [course_or_fail(course)] if course else list(config.load_courses(paths()).values())
    out = {}
    for c in cs:
        import yaml as _y
        start = (_y.safe_load((paths().courses / "planner.yaml").read_text()) or {}).get("semester_start")
        r = compute(_vault_dir(), c.slug, c.exam_date, semester_start=start if hasattr(start, "year") else None,
                    short_name=c.short)
        if r is None:
            out[c.slug] = None
            continue
        if write:
            write_readiness(_vault_dir(), r)
        out[c.slug] = {"expected": r.expected_share, "tested": r.tested_share, "confidence": r.confidence,
                       "exam_date": r.exam_date, "topics": [{"name": t.name, "weight": t.weight, "mastery": t.mastery,
                       "concepts": t.concepts, "tested": t.tested, "due": t.due} for t in r.topics],
                       "next": [t.name for t in r.next_actions()], "unmapped": r.unmapped, "text": render(r)}

    def human(o):
        for slug, v in o.items():
            typer.secho(f"== {slug}", fg="green")
            typer.echo(v["text"] if v else "  no exam model yet (run /exam-model in the course's Claude folder)")
    emit(out, as_json, human)


@app.command()
def init(target: Annotated[Path, typer.Argument(help="Folder for your private workspace (courses, data).")] = Path(".")):
    """Create a workspace: study.yaml, courses/ (an example course), CLAUDE.md and the Claude Code commands.

    Existing files are never overwritten. Then: edit study.yaml and courses/, `uv sync`, `study vault init`."""
    import shutil
    tpl = Path(__file__).parent / "templates" / "workspace"
    target = target.expanduser().resolve()
    made = []
    for src in sorted(tpl.rglob("*")):
        if src.is_file():
            dest = target / src.relative_to(tpl)
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                made.append(str(dest.relative_to(target)))
    pp = target / "pyproject.toml"  # so `uv run study …` (used by the Claude commands) works inside the workspace
    if not pp.exists():
        pp.write_text('[project]\nname = "study-workspace"\nversion = "0.1.0"\nrequires-python = ">=3.12,<3.13"\n'
                      'dependencies = ["study-engine @ git+https://github.com/moubipoch/study-engine"]\n\n'
                      '[tool.uv]\npackage = false\n')
        made.append("pyproject.toml")
    gi = target / ".gitignore"
    if not gi.exists():
        gi.write_text("data/\n.venv/\n*.mp4\n")
        made.append(".gitignore")
    typer.echo(f"workspace {target}: {len(made)} files created" + "".join(f"\n  {m}" for m in made))
    typer.echo("next: edit study.yaml + courses/<slug>/course.yaml, then `uv sync` and `uv run study vault init`")


@app.command()
def timer(action: Annotated[str, typer.Argument(help="install | remove | status")] = "status",
          at: Annotated[str, typer.Option(help="Daily time, e.g. 19:00")] = "19:00"):
    """Daily background sync (systemd user timer, Linux): `study sync --no-window` in this workspace.

    It never opens a window; if a login has expired you get a notification to run `study sync` once."""
    import subprocess
    units = Path.home() / ".config" / "systemd" / "user"
    svc, tmr = units / "study-sync.service", units / "study-sync.timer"
    if action == "remove":
        subprocess.run(["systemctl", "--user", "disable", "--now", "study-sync.timer"], capture_output=True)
        svc.unlink(missing_ok=True)
        tmr.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        typer.echo("removed")
        return
    if action == "install":
        root = paths().root
        log = root / "data" / "logs" / "auto_sync.log"
        units.mkdir(parents=True, exist_ok=True)
        import os
        import shutil as _sh
        uv = _sh.which("uv") or "uv"
        svc.write_text(f"[Unit]\nDescription=study: fetch and ingest new course material\nAfter=network-online.target\n\n"
                       f"[Service]\nType=oneshot\nWorkingDirectory={root}\nEnvironment=PATH={os.environ.get('PATH', '/usr/bin:/bin')}\n"
                       f"ExecStart=/bin/sh -c 'mkdir -p {log.parent} && {uv} run study sync --no-window >> {log} 2>&1'\n")
        tmr.write_text(f"[Unit]\nDescription=study daily sync\n\n[Timer]\nOnCalendar=*-*-* {at}\nPersistent=true\n"
                       "RandomizedDelaySec=10min\n\n[Install]\nWantedBy=timers.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        subprocess.run(["systemctl", "--user", "enable", "--now", "study-sync.timer"])
    subprocess.run(["systemctl", "--user", "list-timers", "study-sync.timer", "--no-pager"])


@app.command()
def ui(port: Annotated[int, typer.Option(help="Port on 127.0.0.1.")] = 8090,
       no_browser: Annotated[bool, typer.Option("--no-browser", help="Don't open a browser tab.")] = False):
    """Open the local GUI in your browser."""
    from study.ui.app import main
    main(port=port, show=not no_browser)


if __name__ == "__main__":
    app()
