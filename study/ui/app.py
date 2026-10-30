"""Local web GUI (NiceGUI): `study ui` opens it in the browser at http://127.0.0.1:8090.

Deterministic actions call the same Python code / CLI as the terminal. Reasoning actions run
Claude Code headless (`claude -p`) on the user's subscription — see claude_runner.py.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app, run, ui

from study import config
from study.cite import fmt_time
from study.db import connect
from study.ingest.pipeline import TYPES, relabel_source
from study.queries import course_stats, list_sources, show_citation, source_chunks, source_images
from study.ui import theme
from study.ui.claude_runner import claude_available, run_claude

PATHS: config.Paths | None = None
_ingest_lock = asyncio.Lock()
MD_EXTRAS = ["fenced-code-blocks", "tables", "latex"]


def paths() -> config.Paths:
    assert PATHS is not None
    return PATHS


def db():
    return connect(paths().db)


def courses() -> dict[str, config.Course]:
    return config.load_courses(paths())


def img_url(path: str) -> str:
    return f"/img?path={path}"


@app.get("/img")
def serve_image(path: str):
    """Serve only images the index knows about (page rasters, video frames, note images)."""
    row = db().execute("SELECT 1 FROM images WHERE path=?", (path,)).fetchone()
    if not row or not Path(path).is_file():
        raise HTTPException(404)
    return FileResponse(path)


def loc_label(r: dict) -> str:
    if r["loc_kind"] == "t":
        return fmt_time(r["extent"])
    return f"{int(r['extent'])} {'pages' if r['loc_kind'] == 'p' else 'lines'}"


# ---------------------------------------------------------------- layout

def frame(title: str, course: str | None = None, active: str = ""):
    theme.apply()
    dark = ui.dark_mode(app.storage.user.get("dark", None))

    def toggle_dark():
        dark.value = not bool(dark.value)
        app.storage.user["dark"] = dark.value
    with ui.header().classes("s-header items-center justify-between px-6 py-2 no-wrap"):
        with ui.row().classes("items-center gap-3 no-wrap min-w-0 flex-1"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round dense")
            ui.label(title).classes("text-base font-semibold truncate min-w-0")
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.button(icon="dark_mode", on_click=toggle_dark).props("flat round dense").tooltip("Light / dark")
            ui.button("Ask Claude", icon="auto_awesome",
                      on_click=lambda: ui.navigate.to("/claude" + (f"?course={course}" if course else ""))).props(
                "unelevated").classes("px-4").style("background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white")
    with ui.left_drawer(value=None, fixed=True).props("width=264 bordered=false").classes("s-drawer p-4") as drawer:
        with ui.link(target="/").classes("no-underline flex items-center gap-3 px-2 pb-4"):
            with ui.element("div").classes("s-logo"):
                ui.icon("school").classes("text-lg")
            with ui.column().classes("gap-0"):
                ui.label("study").classes("s-brand").style("color:var(--text)")
                ui.label(str(paths().settings.get("label") or "exam prep")).classes("text-xs").style("color:var(--muted)")
        for icon, label, href, key in [("space_dashboard", "Dashboard", "/", "home"), ("event", "Planner", "/planner", "planner"),
                                       ("auto_awesome", "Ask Claude", "/claude", "claude"), ("insights", "Visualize", "/viz", "viz"),
                                       ("build", "Tools", "/tools", "tools")]:
            with ui.link(target=href).classes("s-nav" + (" active" if active == key else "")):
                ui.icon(icon)
                ui.label(label)
        ui.label("Courses").classes("s-section")
        for c in sorted(courses().values(), key=lambda c: -c.priority):
            color, short, _ = theme.course_style(c.slug)
            with ui.link(target=f"/course/{c.slug}").classes("s-nav" + (" active" if c.slug == course else "")):
                ui.element("div").classes("s-dot").style(f"background:{color};box-shadow:0 0 0 4px {color}22")
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(short).classes("text-xs font-bold").style(f"color:{color}")
                    ui.label(c.name).classes("text-xs truncate").style("color:var(--muted);max-width:170px")


def _semester_week() -> int | None:
    import datetime as _dt
    import yaml as _yaml
    raw = _yaml.safe_load((paths().courses / "planner.yaml").read_text()) or {}
    if not raw.get("semester_start"):
        return None
    return (_dt.date.today() - _dt.date.fromisoformat(str(raw["semester_start"]))).days // 7 + 1


def _course_summary(con, c) -> dict:
    from study.digest import todo
    from study.srs.mastery import mastery
    items = todo(con, c.slug)
    m = [x["mastery"] for x in mastery(con, c) if x["mastery"] is not None]
    one = lambda q, *a: con.execute(q, a).fetchone()[0]  # noqa: E731
    return {"weeks": len(items), "fresh": sum(i.status == "fresh" for i in items),
            "mastery": sum(m) / len(m) if m else None,
            "rec_h": one("SELECT coalesce(sum(extent),0)/3600.0 FROM sources WHERE course=? AND type='recording'", c.slug),
            "exams": one("SELECT count(*) FROM sources WHERE course=? AND type='exam'", c.slug),
            "questions": one("SELECT count(*) FROM questions WHERE course=?", c.slug),
            "cards": one("SELECT count(*) FROM cards WHERE course=?", c.slug)}


def _exam_text(c) -> tuple[str, str]:
    import datetime as _dt
    if c.project_only:
        return "Project course", "rocket_launch"
    if not c.exam_date:
        return "Exam date not set", "event_busy"
    days = (c.exam_date - _dt.date.today()).days
    return (f"Exam in {days} days" if days >= 0 else "Exam passed"), "event"


# ---------------------------------------------------------------- home

@ui.page("/")
def home():
    import datetime as _dt
    from study.plan.planner import PlanError, Settings, build_plan
    from study.srs.fsrs_q import due
    frame("Dashboard", active="home")
    con = db()
    cs = sorted(courses().values(), key=lambda c: -c.priority)
    summ = {c.slug: _course_summary(con, c) for c in cs}
    now = _dt.datetime.now()
    greet = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
    wk = _semester_week()

    with ui.row().classes("w-full items-end justify-between"):
        theme.page_header(greet, now.strftime("%A, %d %B") + (f" · semester week {wk}" if wk else ""))

        async def do_export():
            from study.export.obsidian import export
            res = await run.io_bound(export, db(), courses(), paths().vault / "Library")
            ui.notify(f"Obsidian vault updated: {res['written']} notes, {res['concepts']} concepts", type="positive")
        with ui.row().classes("gap-2"):
            ui.button("Export to Obsidian", icon="hub", on_click=do_export).props("flat")
            ui.button("Plan my week", icon="event", on_click=lambda: ui.navigate.to("/planner")).props("unelevated color=primary")

    hours = sum(s["rec_h"] for s in summ.values())
    weeks = sum(s["weeks"] for s in summ.values())
    fresh = sum(s["fresh"] for s in summ.values())
    n_due = sum(len(due(con, c.slug)) for c in cs)
    with ui.row().classes("w-full gap-4"):
        theme.tile(f"{hours:.0f} h", "lectures transcribed", "graphic_eq", "#6366f1")
        theme.tile(f"{fresh}/{weeks}", "weekly digests ready", "menu_book", "#10b981")
        theme.tile(str(sum(s["questions"] for s in summ.values())), "practice questions", "quiz", "#a855f7")
        theme.tile(str(sum(s["cards"] for s in summ.values())), "Anki cards", "style", "#06b6d4")
        theme.tile(str(n_due), "due for review", "schedule", "#f59e0b")

    with ui.row().classes("w-full gap-5 items-stretch"):
        with ui.element("div").classes("s-card p-6 flex-[3] min-w-[340px]"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Today").classes("s-h2")
                ui.link("Full plan →", "/planner").classes("text-sm no-underline").style("color:var(--accent)")
            try:
                plan = build_plan(con, courses(), Settings.load(paths().courses / "planner.yaml"), _dt.date.today(), 1)
                blocks = plan["blocks"]
            except PlanError:
                blocks = []
            if not blocks:
                ui.label("Nothing planned today. Enjoy the break, or plan your week.").classes("s-sub mt-2")
            for b in blocks:
                color, short, icon = theme.course_style(b["course"]) if b["course"] != "all" else ("#64748b", "ANKI", "style")
                with ui.element("div").classes("s-timeline-item w-full"):
                    ui.label(f"{b['start']}").classes("font-mono text-xs w-12").style("color:var(--muted)")
                    with ui.element("div").classes("s-iconbox").style(f"background:{color}22;color:{color}"):
                        ui.icon(icon)
                    with ui.column().classes("gap-0 flex-1 min-w-0 overflow-hidden"):
                        ui.label(b["title"]).classes("text-sm font-semibold truncate w-full")
                        ui.label(f"{short} · {b['start']}–{b['end']}").classes("text-xs").style("color:var(--muted)")
                    if b["command"].startswith("/"):
                        ui.button(icon="play_arrow", on_click=lambda b=b: ui.navigate.to(
                            command_link(b["course"] if b["course"] != "all" else "", b["command"]))).props(
                            "round unelevated size=sm").style(f"background:{color};color:white").tooltip(b["command"])
        with ui.element("div").classes("s-card p-6 flex-[2] min-w-[300px]"):
            ui.label("Quick actions").classes("s-h2 mb-2")
            for icon, label, sub, href, color in [
                    ("sync", "Weekly sync", "Run /sync in the Claude desktop app", None, "#6366f1"),
                    ("checklist", "Where am I?", "Status and the best next step", claude_link("", "/status"), "#10b981"),
                    ("style", "Anki", "Push new cards, pull your reviews", f"/course/{next(iter(courses()), '')}", "#06b6d4"),
                    ("insights", "Visualize dynamics", "Phase portraits and bifurcations", "/viz", "#f43f5e")]:
                with ui.element("div").classes("s-timeline-item w-full cursor-pointer").on(
                        "click", (lambda h=href: ui.navigate.to(h)) if href else (lambda: ui.notify(
                            "Open Claude Code (desktop app) in this project and type /sync: it uses your logged-in browser.",
                            multi_line=True))):
                    with ui.element("div").classes("s-iconbox").style(f"background:{color}22;color:{color}"):
                        ui.icon(icon)
                    with ui.column().classes("gap-0"):
                        ui.label(label).classes("text-sm font-semibold")
                        ui.label(sub).classes("text-xs").style("color:var(--muted)")

    from study.teach import listing as _lessons
    active = [r for r in _lessons(con) if r["status"] == "active"][:4]
    if active:
        ui.label("Continue learning").classes("s-h2 mt-2")
        with ui.element("div").classes("grid gap-4 w-full").style("grid-template-columns:repeat(auto-fill,minmax(280px,1fr))"):
            for r in active:
                color, short, icon = theme.course_style(r["course"])
                from study.ui.teach_page import teach_url
                with ui.element("div").classes("s-card s-hover p-5 cursor-pointer").on(
                        "click", lambda r=r: ui.navigate.to(teach_url(r["course"], r["target"]))):
                    with ui.row().classes("items-center gap-3 no-wrap w-full"):
                        with ui.element("div").classes("s-iconbox").style(f"background:{color};color:white"):
                            ui.icon("school")
                        with ui.column().classes("gap-0 min-w-0 flex-1"):
                            ui.label(short).classes("text-xs font-bold tracking-wider").style(f"color:{color}")
                            ui.label(r["title"]).classes("font-semibold leading-tight truncate w-full")
                    ui.label(f"Next: {r['next']}" if r["next"] else "Finished").classes("text-xs mt-3 truncate").style(
                        "color:var(--muted)")
                    with ui.row().classes("w-full items-center gap-2 no-wrap mt-1"):
                        with ui.element("div").classes("flex-1"):
                            theme.bar(r["position"] / r["total"] if r["total"] else 0, color)
                        ui.label(f"{r['position']}/{r['total']}").classes("text-xs").style("color:var(--muted)")

    ui.label("Courses").classes("s-h2 mt-2")
    with ui.element("div").classes("grid gap-5 w-full").style("grid-template-columns:repeat(auto-fill,minmax(300px,1fr))"):
        for c in cs:
            s = summ[c.slug]
            color, short, icon = theme.course_style(c.slug)
            exam, exam_icon = _exam_text(c)
            with ui.element("div").classes("s-card s-hover p-5 cursor-pointer").on(
                    "click", lambda c=c: ui.navigate.to(f"/course/{c.slug}")).style(f"border-top:3px solid {color}!important"):
                with ui.row().classes("items-center gap-3 no-wrap w-full"):
                    with ui.element("div").classes("s-iconbox").style(f"background:{color};color:white"):
                        ui.icon(icon)
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(short).classes("text-xs font-bold tracking-wider").style(f"color:{color}")
                        ui.label(c.name).classes("font-semibold leading-tight")
                with ui.row().classes("gap-2 mt-3"):
                    theme.chip(exam, exam_icon)
                    theme.chip(f"{s['rec_h']:.0f} h", "graphic_eq")
                    if s["exams"]:
                        theme.chip(f"{s['exams']} exam files", "history_edu")
                with ui.column().classes("w-full gap-1 mt-4"):
                    with ui.row().classes("w-full justify-between text-xs").style("color:var(--muted)"):
                        ui.label("Digests")
                        ui.label(f"{s['fresh']}/{s['weeks']}")
                    theme.bar(s["fresh"] / s["weeks"] if s["weeks"] else 0, color)
                    with ui.row().classes("w-full justify-between text-xs mt-2").style("color:var(--muted)"):
                        ui.label("Mastery")
                        ui.label(f"{s['mastery']:.0%}" if s["mastery"] is not None else "no practice yet")
                    theme.bar(s["mastery"], color)


# ---------------------------------------------------------------- course page

@ui.page("/course/{slug}")
def course_page(slug: str):
    try:
        course = config.get_course(paths(), slug)
    except config.ConfigError as e:
        ui.label(str(e))
        return
    frame(course.name, slug)
    color, short, icon = theme.course_style(slug)
    s = _course_summary(db(), course)
    exam, exam_icon = _exam_text(course)
    with ui.element("div").classes("s-hero w-full").style(
            f"background:linear-gradient(135deg,{color},{color}cc 45%,#0f172a)"):
        with ui.row().classes("items-center gap-4 no-wrap"):
            with ui.element("div").classes("s-iconbox").style("background:rgba(255,255,255,.18);width:56px;height:56px;border-radius:16px"):
                ui.icon(icon).classes("text-3xl")
            with ui.column().classes("gap-1"):
                ui.label(f"{short}" + (f" · {course.code}" if course.code else "")).classes("text-xs font-bold tracking-widest opacity-80")
                ui.label(course.name).classes("text-3xl font-extrabold tracking-tight leading-tight")
        nxt = next_lesson(db(), course)
        with ui.row().classes("gap-2 mt-4 items-center w-full"):
            theme.chip(exam, exam_icon)
            theme.chip(f"{s['rec_h']:.0f} h of lectures", "graphic_eq")
            theme.chip(f"{s['fresh']}/{s['weeks']} digests", "menu_book")
            theme.chip(f"{s['questions']} questions", "quiz")
            theme.chip(f"{s['cards']} cards", "style")
            if s["mastery"] is not None:
                theme.chip(f"mastery {s['mastery']:.0%}", "insights")
            if nxt:
                ui.space()
                ui.button(nxt[0], icon="school", on_click=lambda: ui.navigate.to(nxt[1])).props(
                    "unelevated no-caps").classes("px-5 relative z-10").style(f"background:white;color:{color};font-weight:700")
    with ui.tabs().props("dense no-caps inline-label outside-arrows mobile-arrows") as tabs:
        t_search = ui.tab("Search", icon="search")
        t_dig = ui.tab("Learn", icon="school")
        t_prac = ui.tab("Practice", icon="edit_note")
        t_cards = ui.tab("Flashcards", icon="style")
        t_prog = ui.tab("Progress", icon="insights")
        t_topics = ui.tab("Topics", icon="checklist")
        t_src = ui.tab("Sources", icon="folder")
        t_add = ui.tab("Add material", icon="upload")
        t_view = ui.tab("Viewer", icon="visibility")
    with ui.tab_panels(tabs, value=t_dig).classes("w-full"):
        with ui.tab_panel(t_search):
            search_panel(course)
        with ui.tab_panel(t_dig):
            digests_panel(course)
        with ui.tab_panel(t_prac):
            practice_panel(course)
        with ui.tab_panel(t_cards):
            flashcards_panel(course)
        with ui.tab_panel(t_prog):
            progress_panel(course)
        with ui.tab_panel(t_topics):
            topics_panel(course)
        with ui.tab_panel(t_src):
            sources_panel(course)
        with ui.tab_panel(t_add):
            add_panel(course)
        with ui.tab_panel(t_view):
            viewer_panel(course)


def claude_link(course: str, prompt: str) -> str:
    from urllib.parse import urlencode
    return "/claude?" + urlencode({"course": course, "q": prompt, "auto": 1})


def command_link(course: str, command: str) -> str:
    """Where a planned command opens: /teach goes to the lesson page, everything else to Ask Claude."""
    import shlex
    if command.startswith("/teach "):
        parts = shlex.split(command)[1:]
        if len(parts) >= 2:
            from study.ui.teach_page import teach_url
            return teach_url(parts[0], " ".join(parts[1:]))
    return claude_link(course, command)


def next_lesson(con, course) -> tuple[str, str] | None:
    """(label, url) of the lesson to do next: the most recent unfinished one, else the first week not yet learned."""
    from study.digest import todo
    from study.teach import listing
    from study.ui.teach_page import teach_url
    if course.project_only:
        return None
    active = [r for r in listing(con, course.slug) if r["status"] == "active"]
    if active:
        r = active[0]
        return f"Continue: {r['title']}", teach_url(course.slug, r["target"])
    done = {r["target"] for r in listing(con, course.slug) if r["status"] == "done"}
    reached = _semester_week()
    for it in todo(con, course.slug):
        if f"W{it.lecture:02d}" not in done and (reached is None or it.lecture <= reached):
            return f"Learn week {it.lecture}", teach_url(course.slug, str(it.lecture))
    return None


def cite_dialog(cite: str):
    res = show_citation(db(), cite, context=1)
    with ui.dialog() as d, ui.card().classes("w-full max-w-4xl p-6 gap-3"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(cite).classes("font-mono text-sm text-primary break-all")
            ui.button(icon="close", on_click=d.close).props("flat round dense")
        out = ui.column().classes("w-full gap-3")
        _render_citation(res, out, "w-72")
    d.open()


def search_panel(course: config.Course):
    from study.retrieve.embed import get_embedder
    from study.retrieve.search import Filters, search

    with ui.element("div").classes("s-card p-3 w-full"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            q = ui.input(placeholder="Search lectures, slides, exams and code: concepts, identifiers, questions…").classes(
                "flex-grow").props("borderless clearable autofocus input-class=text-base").style("padding-left:8px")
            types = ui.select(list(TYPES), multiple=True, label="Types", value=[]).classes("w-52").props("dense outlined use-chips")
            week = ui.number("Week", format="%d", min=0).classes("w-24").props("dense outlined")
            go = ui.button(icon="search").props("unelevated round color=primary")
    status = ui.label("").classes("text-xs text-gray-500")
    results = ui.column().classes("w-full gap-3")
    with results:
        with ui.column().classes("w-full items-center py-10 gap-2"):
            ui.icon("travel_explore").classes("text-5xl").style("color:var(--muted)")
            ui.label("Ask in your own words: search understands meaning, not just keywords").classes("s-sub")
            with ui.row().classes("gap-2 mt-1"):
                for ex in ["main idea of this week", "definition", "typical exam question"]:
                    async def pick_example(ex=ex):
                        q.set_value(ex)
                        await run_search()
                    ui.button(ex, on_click=pick_example).props("flat no-caps size=sm rounded").style(
                        "background:var(--code-bg)")

    async def run_search():
        text = (q.value or "").strip()
        if not text:
            return
        status.set_text("searching…")
        flt = Filters(int(week.value) if week.value is not None else None, list(types.value or []))

        def work():
            try:
                emb = get_embedder(str(paths().cache / "models"))
            except Exception:
                emb = None
            return search(db(), course.slug, text, k=12, flt=flt, embedder=emb, max_chars=700,
                          mode="hybrid" if emb else "lexical")
        res = await run.io_bound(work)
        results.clear()
        status.set_text(f"{len(res['results'])} results ({res['mode']})" + (f" — {res['warning']}" if res["warning"] else ""))
        with results:
            ui.button("Ask Claude about this", icon="psychology",
                      on_click=lambda: ui.navigate.to(claude_link(course.slug, f"/ask {course.slug} {text}"))
                      ).props("outline")
            for r in res["results"]:
                with ui.card().classes("w-full"):
                    with ui.row().classes("w-full items-start justify-between no-wrap"):
                        with ui.column().classes("gap-1 flex-grow"):
                            with ui.row().classes("items-center gap-2"):
                                ui.badge(r["type"])
                                ui.label(r["cite"]).classes("font-mono text-sm text-primary cursor-pointer").on(
                                    "click", lambda c=r["cite"]: cite_dialog(c))
                                ui.button(icon="content_copy", on_click=lambda c=r["cite"]: (
                                    ui.clipboard.write(c), ui.notify("citation copied"))).props("flat dense size=sm")
                            if r["heading"]:
                                ui.label(r["heading"]).classes("font-semibold")
                            ui.label(" ".join(r["text"].split())[:500]).classes("text-sm")
                        if r["images"]:
                            ui.image(img_url(r["images"][0])).classes("w-48 cursor-zoom-in").on(
                                "click", lambda p=r["images"][0]: zoom(p))

    go.on_click(run_search)
    q.on("keydown.enter", run_search)


def topics_panel(course: config.Course):
    from study.topics import TopicError, approve, normalise, remove
    color = theme.course_style(course.slug)[0]
    box = ui.column().classes("w-full gap-4")

    def render():
        c = config.get_course(paths(), course.slug)
        box.clear()
        with box:
            right = theme.section("Topics", "Claude proposes a list with evidence from slides and exercises; nothing "
                                  "is official until you approve it.", "checklist", color)
            with right:
                ui.button("Propose with Claude", icon="auto_awesome",
                          on_click=lambda: ui.navigate.to(claude_link(c.slug, f"/topics {c.slug}"))
                          ).props("unelevated color=primary")
            proposed = normalise(c.topics_proposed)
            if proposed:
                checks = {}
                with ui.element("div").classes("s-card p-5 w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(f"Proposed · {len(proposed)}").classes("s-h2")

                        def do_approve(only_checked: bool):
                            names = [n for n, cb in checks.items() if cb.value] if only_checked else None
                            if only_checked and not names:
                                ui.notify("Nothing selected")
                                return
                            try:
                                took, _ = approve(config.get_course(paths(), course.slug), names)
                            except TopicError as e:
                                ui.notify(str(e), type="negative")
                                return
                            ui.notify(f"Approved {len(took)} topics", type="positive")
                            render()
                        with ui.row().classes("gap-2"):
                            ui.button("Approve selected", icon="check", on_click=lambda: do_approve(True)
                                      ).props("unelevated color=positive")
                            ui.button("Approve all", on_click=lambda: do_approve(False)).props("flat")
                    for t in proposed:
                        with ui.element("div").classes("s-timeline-item w-full items-start"):
                            checks[t["name"]] = ui.checkbox(value=True).props("dense")
                            with ui.column().classes("gap-1 flex-1 min-w-0"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(t["name"]).classes("font-semibold")
                                    for w in t["lectures"]:
                                        theme.pill(f"W{w}", color)
                                if t["summary"]:
                                    ui.label(t["summary"]).classes("text-sm").style("color:var(--muted)")
                                with ui.row().classes("gap-2"):
                                    for ct in t["cites"]:
                                        ui.label(ct).classes("font-mono text-xs text-primary cursor-pointer").on(
                                            "click", lambda ct=ct: cite_dialog(ct))
            approved = normalise(c.topics)
            if not approved:
                theme.empty("checklist", "No approved topics yet",
                            "Topics drive mastery tracking, practice selection and the planner.")
                return
            with ui.element("div").classes("s-card p-5 w-full"):
                ui.label(f"Approved · {len(approved)}").classes("s-h2 mb-2")
                with ui.element("div").classes("grid gap-2 w-full").style(
                        "grid-template-columns:repeat(auto-fill,minmax(300px,1fr))"):
                    for t in approved:
                        with ui.element("div").classes("s-timeline-item").style(f"border-left:3px solid {color}"):
                            with ui.column().classes("gap-0 flex-1 min-w-0"):
                                ui.label(t["name"]).classes("text-sm font-semibold")
                                if t["lectures"]:
                                    ui.label(", ".join(f"week {w}" for w in t["lectures"])).classes("text-xs").style(
                                        "color:var(--muted)")
                            ui.button(icon="close", on_click=lambda n=t["name"]: (
                                remove(config.get_course(paths(), course.slug), [n]), render())
                                      ).props("flat round dense size=sm").tooltip("Remove")
    render()


def linkify_cites(md: str) -> str:
    """[@course:key#loc] -> clickable link to the citation viewer."""
    from urllib.parse import quote
    from study.cite import CITE_RE
    return CITE_RE.sub(lambda m: f"[{m.group('key')}#{m.group('loc')}](/cite?c={quote(m.group(0))})", md)


def strip_frontmatter(text: str) -> str:
    from study.digest import _strip_frontmatter
    return _strip_frontmatter(text)


def render_md(text: str):
    ui.markdown(linkify_cites(strip_frontmatter(text)), extras=MD_EXTRAS).classes("w-full digest")


def digests_panel(course: config.Course):
    """The Learn tab: one card per week with its interactive lesson and its written digest."""
    from study.digest import todo
    from study.teach import listing
    from study.brief import status as brief_status
    from study.ui.teach_page import teach_url
    from study.ui.week_page import week_url
    color = theme.course_style(course.slug)[0]
    items = todo(db(), course.slug)
    lessons = {r["target"]: r for r in listing(db(), course.slug)}
    briefs = brief_status(db(), course.slug)
    right = theme.section("Learn", "Open a week for a short overview of the lecture, then ask follow-up questions to go "
                          "deeper. Teach me: a step-by-step lesson with check questions.", "school", color)
    if not items:
        theme.empty("school", "No lecture material with week numbers yet",
                    "Add slides or recordings and set their week in Sources.")
        return
    checks = {}

    def generate(weeks: list[int] | None):
        arg = " ".join(map(str, weeks)) if weeks else "next 2"
        ui.navigate.to(claude_link(course.slug, f"/digest {course.slug} {arg}"))
    with right:
        if (course.dir / "glossary.md").exists():
            ui.button("Glossary", icon="translate", on_click=lambda: ui.navigate.to(f"/doc/{course.slug}/glossary")
                      ).props("flat")
            ui.button("Key results", icon="star", on_click=lambda: ui.navigate.to(f"/doc/{course.slug}/key_results")
                      ).props("flat")
        ui.button("Write selected digests", icon="edit_note",
                  on_click=lambda: generate([w for w, cb in checks.items() if cb.value]) if any(
                      cb.value for cb in checks.values()) else ui.notify("Tick the weeks first")).props("flat")
        ui.button("Teach me a topic", icon="lightbulb", on_click=lambda: topic_dialog(course)).props("flat")
        if any(v != "fresh" for v in briefs.values()):
            ui.button("Write all overviews", icon="auto_stories", on_click=lambda: ui.navigate.to(
                claude_link(course.slug, f"/brief {course.slug} missing"))).props("outline")
    topic_lessons = [r for r in lessons.values() if r["target"].startswith("topic-")]
    if topic_lessons:
        with ui.row().classes("gap-2 flex-wrap"):
            for r in topic_lessons:
                with ui.link(target=teach_url(course.slug, r["target"])).classes("no-underline"):
                    theme.chip(f"{r['title']} · {r['position']}/{r['total']}", "lightbulb")
    status_color = {"missing": theme.AMBER, "stale": theme.BLUE, "fresh": theme.GREEN}
    with ui.element("div").classes("grid gap-4 w-full").style("grid-template-columns:repeat(auto-fill,minmax(270px,1fr))"):
        for it in items:
            lesson = lessons.get(f"W{it.lecture:02d}")
            with ui.element("div").classes("s-card s-hover p-5 flex flex-col cursor-pointer").on(
                    "click", lambda n=it.lecture: ui.navigate.to(week_url(course.slug, n))):
                with ui.row().classes("w-full items-center justify-between no-wrap"):
                    with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                        with ui.element("div").classes("s-iconbox").style(
                                f"background:color-mix(in srgb,{color} 14%,transparent);color:{color};font-weight:800"):
                            ui.label(f"{it.lecture:02d}")
                        ui.label(lesson["title"] if lesson else f"Week {it.lecture}").classes("font-semibold truncate")
                ui.label(", ".join(it.sources)).classes("text-xs mt-2 line-clamp-2").style("color:var(--muted)")
                with ui.row().classes("w-full items-center gap-2 no-wrap mt-3"):
                    if lesson:
                        frac = lesson["position"] / lesson["total"] if lesson["total"] else 0
                        with ui.element("div").classes("flex-1"):
                            theme.bar(frac, theme.GREEN if lesson["status"] == "done" else color)
                        ui.label("learned" if lesson["status"] == "done" else f"{lesson['position']}/{lesson['total']}"
                                 ).classes("text-xs").style("color:var(--muted)")
                    else:
                        ui.label("not learned yet").classes("text-xs flex-1").style("color:var(--muted)")
                    b = briefs.get(it.lecture, "missing")
                    theme.pill("overview" if b == "fresh" else "no overview yet" if b == "missing" else "overview outdated",
                               status_color[b])
                ui.element("div").classes("flex-1")
                with ui.row().classes("w-full items-center gap-2 mt-3 no-wrap"):
                    with ui.element("div").on("click.stop", lambda: None):
                        ui.button("Overview", icon="auto_stories", on_click=lambda n=it.lecture: ui.navigate.to(
                            week_url(course.slug, n))).props("unelevated no-caps dense").classes("px-3").style(
                            f"background:{color};color:white")
                    with ui.element("div").on("click.stop", lambda: None):
                        label = ("Lesson done" if lesson["status"] == "done" else "Continue lesson") if lesson else "Teach me"
                        ui.button(label, icon="school", on_click=lambda n=it.lecture: ui.navigate.to(
                            teach_url(course.slug, str(n)))).props("flat no-caps dense")
                    ui.space()
                    if it.status != "missing":
                        with ui.element("div").on("click.stop", lambda: None):
                            ui.button(icon="menu_book", on_click=lambda n=it.lecture: ui.navigate.to(
                                f"/digest/{course.slug}/{n}")).props("flat round dense size=sm").tooltip("Full digest")
                    if it.status != "fresh":
                        with ui.element("div").on("click.stop", lambda: None):
                            checks[it.lecture] = ui.checkbox(value=False).props("dense size=sm").tooltip(
                                "Select to write this week's full digest")


def topic_dialog(course: config.Course):
    from study.config import topic_name
    from study.ui.teach_page import teach_url
    names = [topic_name(t) for t in course.topics]
    with ui.dialog() as d, ui.card().classes("w-full max-w-lg p-6 gap-3"):
        ui.label("Teach me a topic").classes("s-h2")
        ui.label("Pick an approved topic or type any concept; Claude gathers it from every week.").classes("s-sub")
        sel = ui.select(names, with_input=True, new_value_mode="add-unique", label="Topic or concept").props(
            "outlined").classes("w-full")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=d.close).props("flat")
            ui.button("Start lesson", icon="school", on_click=lambda: sel.value and ui.navigate.to(
                teach_url(course.slug, sel.value))).props("unelevated color=primary")
    d.open()


def practice_panel(course: config.Course):
    from study.practice.questions import listing, new_session, pick
    color = theme.course_style(course.slug)[0]
    rows = listing(db(), course.slug)
    style = course.style or {}
    if course.project_only:
        theme.empty("science", "Project-only course: no mock exams",
                    "Use Ask Claude (/project) for literature digests, experiment planning and code review.")
        ui.button("Open project assistant", icon="auto_awesome", on_click=lambda: ui.navigate.to(
            claude_link(course.slug, f"/project {course.slug}"))).props("unelevated color=primary")
        return
    right = theme.section("Practice", "Exam-calibrated questions; code answers are compiled and tested when graded.",
                          "edit_note", color)
    with right:
        ui.button("Parse past exams", icon="history_edu", on_click=lambda: ui.navigate.to(
            claude_link(course.slug, f"/parse-exams {course.slug}"))).props("flat")
        if "cpp" in (style.get("languages") or []):
            kinds = style.get("exercise_kinds") or ["find-bug"]
            kind_sel = ui.select(kinds, value=kinds[0]).props("outlined dense").classes("w-48")
            ui.button("Code exercise", icon="code", on_click=lambda: ui.navigate.to(claude_link(
                course.slug, f"/exercise {course.slug} {kind_sel.value} n=2"))).props("outline")
        ui.button("Generate questions", icon="auto_awesome", on_click=lambda: ui.navigate.to(
            claude_link(course.slug, f"/quiz {course.slug} n=5"))).props("unelevated color=primary")
    if not rows:
        theme.empty("quiz", "The question bank is empty",
                    "Parse past exams first: generated questions are then calibrated to the real exam style.")
    else:
        with ui.element("div").classes("s-card p-5 w-full"):
            with ui.row().classes("items-end gap-3 w-full"):
                with ui.column().classes("gap-0"):
                    ui.label("Start a session").classes("s-h2")
                    ui.label("Due and weak questions come first").classes("text-xs").style("color:var(--muted)")
                ui.space()
                n = ui.number("Questions", value=3, min=1, max=10, format="%d").props("outlined dense").classes("w-28")
                week = ui.number("Week", format="%d", min=0).props("outlined dense clearable").classes("w-24")
                kinds = sorted({r["kind"] for r in rows})
                kind = ui.select(["any", *kinds], value="any", label="Kind").props("outlined dense").classes("w-44")

                def start():
                    qids = [r["id"] for r in pick(db(), course.slug, int(n.value or 3),
                                                  lecture=int(week.value) if week.value is not None else None,
                                                  kind=None if kind.value == "any" else kind.value)]
                    if not qids:
                        ui.notify("No questions match that filter", type="warning")
                        return
                    sdir = new_session(db(), course, qids)
                    ui.navigate.to(f"/practice/{course.slug}/{sdir.name}")
                ui.button("Start", icon="play_arrow", on_click=start).props("unelevated color=positive")
        columns = [{"name": c, "label": l, "field": c, "sortable": True, "align": "left"} for c, l in
                   [("lecture", "Week"), ("title", "Question"), ("kind", "Kind"), ("difficulty", "Difficulty"),
                    ("points", "Pts"), ("n_attempts", "Tries"), ("last", "Last")]]
        for r in rows:
            r["last"] = f"{r['last_score']:.0%}" if r["last_score"] is not None else "new"
        ui.table(columns=columns, rows=rows, row_key="id", pagination=15).classes("w-full").props("flat")
    sessions = sorted((course.dir / "practice").glob("*/session.json"), reverse=True)[:8]
    if sessions:
        ui.label("Recent sessions").classes("s-h2 mt-2")
        with ui.row().classes("gap-2 flex-wrap"):
            for sj in sessions:
                with ui.link(target=f"/practice/{course.slug}/{sj.parent.name}").classes("no-underline"):
                    theme.chip(sj.parent.name, "history")


def flashcards_panel(course: config.Course):
    from study.srs.anki import AnkiConnect, AnkiError, export_apkg, pull, push, struggling
    from study.srs.cards import list_cards
    from study.tools import ankiconnect_version
    color = theme.course_style(course.slug)[0]
    cards = list_cards(db(), course.slug)
    connected = ankiconnect_version() is not None
    hard = struggling(db(), course.slug)
    with ui.row().classes("w-full gap-4"):
        theme.tile(str(len(cards)), f"cards · deck {course.anki_deck}", "style", color)
        theme.tile(str(len(hard)), "struggling cards", "local_fire_department", theme.RED)
        theme.tile("on" if connected else "off", "AnkiConnect" + ("" if connected else " (start Anki)"),
                   "link" if connected else "link_off", theme.GREEN if connected else theme.GREY)
    right = theme.section("Anki", "Cards live in Anki; Claude explains the ones you keep forgetting and only edits "
                          "after you confirm.", "style", color)

    async def do(fn, label):
        try:
            res = await run.io_bound(fn, db(), course.slug, course.anki_deck, AnkiConnect())
            ui.notify(f"{label}: {res}", type="positive")
        except AnkiError as e:
            ui.notify(str(e), type="negative", multi_line=True)

    def export():
        out = paths().data / "anki" / f"{course.slug}.apkg"
        n = export_apkg(db(), course.slug, course.anki_deck, out)
        ui.download(out, f"{course.slug}.apkg")
        ui.notify(f"Exported {n} cards: import the .apkg in Anki (File → Import)")
    with right:
        ui.button("Export .apkg", icon="save_alt", on_click=export).props("flat")
        ui.button("Pull reviews", icon="download", on_click=lambda: do(pull, "pulled")).props("flat")
        ui.button("Push", icon="upload", on_click=lambda: do(push, "pushed")).props("outline")
        ui.button("Make cards", icon="auto_awesome", on_click=lambda: ui.navigate.to(
            claude_link(course.slug, f"/cards {course.slug}"))).props("unelevated color=primary")
    if hard:
        with ui.element("div").classes("s-card p-5 w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Struggling").classes("s-h2")
                ui.button("Help with all", icon="support", on_click=lambda: ui.navigate.to(
                    claude_link(course.slug, f"/anki {course.slug}"))).props("flat")
            for c in hard:
                with ui.element("div").classes("s-timeline-item w-full").style(f"border-left:3px solid {theme.RED}"):
                    ui.label(("⚑ " if c["flags"] else "") + c["front"][:140]).classes("text-sm flex-1 min-w-0 truncate")
                    theme.pill(f"{c['lapses']} lapses", theme.RED)
                    ui.button(icon="auto_awesome", on_click=lambda cid=c["id"]: ui.navigate.to(
                        claude_link(course.slug, f"/anki {course.slug} card {cid}"))).props(
                        "flat round dense size=sm").tooltip("Explain with Claude")
    if cards:
        cols = [{"name": k, "label": l, "field": k, "sortable": True, "align": "left"} for k, l in
                [("lecture", "Week"), ("topic", "Topic"), ("front", "Front"), ("reps", "Reps"), ("lapses", "Lapses"),
                 ("interval", "Interval")]]
        ui.table(columns=cols, rows=cards, row_key="id", pagination=15).classes("w-full").props("flat")
    else:
        theme.empty("style", "No cards yet", "Digest a week first, then make cards from it.")


def progress_panel(course: config.Course):
    from study.srs.fsrs_q import due
    from study.srs.mastery import mastery
    color = theme.course_style(course.slug)[0]
    rows = mastery(db(), course)
    n_due = len(due(db(), course.slug))
    known = [r["mastery"] for r in rows if r["mastery"] is not None]
    with ui.row().classes("w-full gap-4"):
        theme.tile(f"{sum(known) / len(known):.0%}" if known else "—", "average mastery", "insights", color)
        theme.tile(str(n_due), "questions due", "event_repeat", theme.AMBER)
        theme.tile(f"{len(known)}/{len(rows)}", "topics with evidence", "fact_check", theme.BLUE)
    right = theme.section("Mastery per topic", "60% recency-weighted practice scores + 40% Anki recall (30 days). "
                          "Weakest first.", "insights", color)
    if n_due:
        with right:
            ui.button("Practise due questions", icon="play_arrow", on_click=lambda: ui.navigate.to(
                claude_link(course.slug, f"/practice {course.slug} n={min(n_due, 5)}"))).props("unelevated color=positive")
    if not rows:
        theme.empty("insights", "No topics yet", "Approve a topic list in the Topics tab to track mastery.")
        return
    with ui.element("div").classes("s-card p-3 w-full"):
        for r in rows:
            m = r["mastery"]
            with ui.element("div").classes("s-timeline-item w-full"):
                with ui.column().classes("gap-1 flex-1 min-w-0"):
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        ui.label(r["topic"]).classes("text-sm font-semibold truncate")
                        ui.label("no evidence yet" if m is None else f"{m:.0%}").classes("text-sm font-bold").style(
                            f"color:{theme.level_color(m)}")
                    theme.bar(m, theme.level_color(m))
                    ui.label(f"{r['attempts']} attempts · {r['reviews']} reviews").classes("text-xs").style(
                        "color:var(--muted)")
                ui.button(icon="school", on_click=lambda t=r["topic"]: ui.navigate.to(
                    command_link(course.slug, f'/teach {course.slug} "{t}"'))).props(
                    "flat round dense size=sm").tooltip("Teach me this topic")
                ui.button(icon="play_arrow", on_click=lambda t=r["topic"]: ui.navigate.to(
                    claude_link(course.slug, f'/practice {course.slug} topic "{t}"'))).props(
                    "flat round dense size=sm").tooltip("Practise this topic")


def sources_panel(course: config.Course):
    color = theme.course_style(course.slug)[0]
    columns = [
        {"name": "lec", "label": "Week", "field": "lec", "sortable": True, "align": "left"},
        {"name": "type", "label": "Type", "field": "type", "sortable": True, "align": "left"},
        {"name": "key", "label": "Key", "field": "key", "sortable": True, "align": "left"},
        {"name": "size", "label": "Size", "field": "size", "align": "right"},
        {"name": "chunks", "label": "Chunks", "field": "chunks", "align": "right"},
        {"name": "images", "label": "Images", "field": "images", "align": "right"},
        {"name": "flag", "label": "", "field": "flag", "align": "left"},
    ]

    def rows():
        return [{"lec": f"L{r['lecture']:02d}" if r["lecture"] is not None else "", "type": r["type"],
                 "key": r["key"], "size": loc_label(r), "chunks": r["chunks"], "images": r["images"],
                 "flag": "needs transcription" if r["meta"].get("needs_transcription") else ""}
                for r in list_sources(db(), course.slug)]

    data = rows()
    right = theme.section(f"Sources · {len(data)}", "Click a row to open it, change its week/key/type, or remove it "
                          "from the index.", "folder", color)
    with right:
        search = ui.input(placeholder="Filter…").props("outlined dense clearable").classes("w-64")
        with search.add_slot("prepend"):
            ui.icon("filter_list")
    table = ui.table(columns=columns, rows=data, row_key="key", pagination=25).classes("w-full cursor-pointer").props("flat")
    table.add_slot("body-cell-type", r'''<q-td :props="props"><span class="s-chip">{{ props.value }}</span></q-td>''')
    table.add_slot("body-cell-key", r'''<q-td :props="props"><span class="font-mono text-xs">{{ props.value }}</span></q-td>''')
    table.add_slot("body-cell-flag", r'''<q-td :props="props"><span v-if="props.value" class="s-chip" style="color:#f59e0b">{{ props.value }}</span></q-td>''')
    table.bind_filter_from(search, "value")

    def refresh():
        table.rows = rows()
        table.update()

    table.on("rowClick", lambda e: source_dialog(course, e.args[1]["key"], refresh))


def source_dialog(course: config.Course, key: str, refresh):
    con = db()
    src = next((r for r in list_sources(con, course.slug) if r["key"] == key), None)
    if not src:
        return
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-5xl p-6 gap-3"):
        with ui.row().classes("w-full items-start justify-between no-wrap"):
            with ui.column().classes("gap-1 min-w-0"):
                ui.label(key).classes("text-xl font-bold font-mono break-all")
                with ui.row().classes("gap-2"):
                    theme.chip(src["type"], "category")
                    theme.chip(loc_label(src), "straighten")
                    if src["lecture"] is not None:
                        theme.chip(f"week {src['lecture']}", "event")
                ui.label(src["path"]).classes("text-xs break-all").style("color:var(--muted)")
            ui.button(icon="close", on_click=dlg.close).props("flat round")
        with ui.expansion("Edit label", icon="edit").classes("w-full"):
            with ui.row().classes("items-end gap-2"):
                new_key = ui.input("Key", value=key).props("outlined dense")
                week = ui.number("Week (lecture no.)", value=src["lecture"], format="%d", min=0).props("outlined dense clearable")
                typ = ui.select(list(TYPES), value=src["type"], label="Type").props("outlined dense")

                def save():
                    try:
                        relabel_source(db(), course.slug, key, new_key=new_key.value if new_key.value != key else None,
                                       lecture=int(week.value) if week.value is not None else None,
                                       clear_lecture=week.value is None, typ=typ.value)
                    except (KeyError, ValueError) as e:
                        ui.notify(str(e), type="negative")
                        return
                    ui.notify("Saved", type="positive")
                    dlg.close()
                    refresh()
                ui.button("Save", icon="check", on_click=save).props("unelevated color=primary")

                def forget():
                    with db() as c:
                        c.execute("DELETE FROM sources WHERE course=? AND key=?", (course.slug, key))
                    ui.notify(f"Removed {key} from the index (file untouched)")
                    dlg.close()
                    refresh()
                ui.button("Remove from index", icon="delete_outline", on_click=forget, color="negative").props("flat")
        imgs = source_images(con, course.slug, key)
        if imgs:
            with ui.expansion(f"Images ({len(imgs)})", icon="image").classes("w-full"):
                with ui.row().classes("flex-wrap gap-2"):
                    for im in imgs[:120]:
                        label = fmt_time(im["loc"]) if src["loc_kind"] == "t" else f"p{int(im['loc'])}"
                        with ui.column().classes("items-center gap-0"):
                            ui.image(img_url(im["path"])).classes("w-56 rounded-lg cursor-zoom-in").on(
                                "click", lambda p=im["path"]: zoom(p))
                            ui.label(label).classes("text-xs").style("color:var(--muted)")
        with ui.scroll_area().classes("w-full h-96 rounded-xl").style("background:var(--code-bg)"):
            for ch in source_chunks(con, course.slug, key):
                if src["loc_kind"] == "t":
                    loc = f"{fmt_time(ch['loc_start'])}–{fmt_time(ch['loc_end'])}"
                else:
                    a, b = int(ch["loc_start"]), int(ch["loc_end"])
                    loc = f"{src['loc_kind']}{a}" + (f"-{b}" if b != a else "")
                ui.label(f"{loc}  {ch['heading']}").classes("text-xs font-mono text-primary mt-3")
                if src["type"] == "code":
                    ui.code(ch["text"], language="cpp" if key.endswith((".cpp", ".hpp", ".h", ".cc")) else None
                            ).classes("w-full text-xs")
                else:
                    ui.label(ch["text"]).classes("text-sm whitespace-pre-wrap")
    dlg.open()


def zoom(path: str):
    with ui.dialog().props("maximized") as d, ui.card().classes("items-center").style("background:rgba(0,0,0,.85)"):
        ui.button(icon="close", on_click=d.close).props("flat round color=white").classes("self-end")
        ui.image(img_url(path)).classes("max-w-full").props("fit=contain")
    d.open()


def add_panel(course: config.Course):
    color = theme.course_style(course.slug)[0]
    theme.section("Add material", "Slides, recordings, exercise sheets, code, notes, images, a folder or a YouTube URL. "
                  "Re-adding unchanged files is harmless: they're skipped.", "upload", color)
    with ui.element("div").classes("s-card p-5 w-full"):
        with ui.row().classes("items-end gap-3 w-full"):
            typ = ui.select(["auto", *TYPES], value="auto", label="Type").props("outlined dense").classes("w-40")
            week = ui.number("Week (optional)", format="%d", min=0).props("outlined dense clearable").classes("w-36")
            key = ui.input("Key / prefix (optional)").props("outlined dense").classes("w-56").tooltip(
                "Key for one file, or a prefix like 'mine' for a folder")
            frames = ui.switch("Video slide snapshots", value=True)
    log = ui.log(max_lines=500).classes("w-full h-64 font-mono text-xs rounded-xl p-3").style(
        "background:#0b1020;color:#cbd5e1;border:1px solid var(--border)")

    async def do_ingest(target: str):
        if _ingest_lock.locked():
            ui.notify("Another import is running; queued.", type="info")
        async with _ingest_lock:
            cmd = [sys.executable, "-m", "study.cli", "ingest", course.slug, target, "--json"]
            if typ.value != "auto":
                cmd += ["--type", typ.value]
            if week.value is not None:
                cmd += ["--lecture", str(int(week.value))]
            if key.value:
                cmd += ["--key", key.value]
            if not frames.value:
                cmd += ["--no-frames"]
            log.push(f"$ study ingest {course.slug} {target}")
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(paths().root), stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            out_task = asyncio.create_task(proc.stdout.read())
            buf, last_progress = b"", ""
            while chunk := await proc.stderr.read(512):
                buf += chunk
                *parts, buf = re.split(rb"[\r\n]", buf)
                for p in parts:
                    line = p.decode(errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("[transcribe") and line[:20] == last_progress[:20]:
                        if line[-6:].strip().split(".")[0] != last_progress[-6:].strip().split(".")[0]:
                            log.push(line)  # one line per whole percent
                    else:
                        log.push(line)
                    last_progress = line
            stdout = await out_task
            await proc.wait()
            try:
                res = json.loads(stdout or b"[]")
            except json.JSONDecodeError:
                res = []
                log.push(stdout.decode(errors="replace"))
            counts: dict[str, int] = {}
            for r in res:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
                if r["status"] in ("added", "updated", "error", "duplicate"):
                    log.push(f"{r['status']:<9} {r['type']:<9} {r['key']} {r['detail']}")
            summary = ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing ingested"
            log.push(f"done: {summary}")
            ui.notify(f"Import finished: {summary}", type="negative" if counts.get("error") else "positive")

    inbox = course.dir / "materials" / "inbox"

    async def on_upload(e):
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / Path(e.file.name).name
        await e.file.save(dest)
        await do_ingest(str(dest))

    ui.upload(label="Drop files here or click to browse", multiple=True, auto_upload=True, on_upload=on_upload,
              max_file_size=4 * 1024**3).classes("w-full s-drop").props("accept=* flat bordered")
    with ui.element("div").classes("s-card p-3 w-full"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.icon("link").classes("text-xl ml-2").style("color:var(--muted)")
            target = ui.input(placeholder="…or a file/folder path on this computer, or a YouTube URL").classes(
                "flex-grow").props("borderless")
            ui.button("Import", icon="download", on_click=lambda: do_ingest(target.value.strip()) if target.value else None
                      ).props("unelevated color=primary")
    log.move(target_index=-1)


def _render_citation(res: dict, out, img_w: str = "w-96"):
    with out:
        if "error" in res:
            with ui.row().classes("items-center gap-2"):
                ui.icon("error_outline").style(f"color:{theme.RED}")
                ui.label(res["error"]).style(f"color:{theme.RED}")
            return
        with ui.element("div").classes("s-card p-5 w-full"):
            for ch in res["chunks"]:
                a, b = ch["loc_start"], ch["loc_end"]
                loc = f"{fmt_time(a)}–{fmt_time(b)}" if res["kind"] == "t" else f"{a:g}–{b:g}"
                ui.label(f"{loc}  {ch['heading']}").classes("text-xs font-mono text-primary mt-2")
                ui.label(ch["text"]).classes("text-sm whitespace-pre-wrap leading-relaxed")
        if res["images"]:
            with ui.row().classes("flex-wrap gap-3"):
                for p in res["images"][:8]:
                    ui.image(img_url(p)).classes(f"{img_w} rounded-xl cursor-zoom-in s-hover").on(
                        "click", lambda p=p: zoom(p))


def _doc_shell(slug: str, kicker: str, back: str, extra=None):
    color = theme.course_style(slug)[0]
    with ui.row().classes("w-full max-w-4xl mx-auto items-center justify-between"):
        ui.button("Back", icon="arrow_back", on_click=lambda: ui.navigate.to(back)).props("flat")
        if extra:
            extra()
    card = ui.element("div").classes("s-card w-full max-w-4xl mx-auto px-10 py-8").style(f"border-top:4px solid {color}")
    with card:
        ui.label(kicker).classes("text-xs font-bold tracking-widest uppercase").style(f"color:{color}")
    return card


def viewer_panel(course: config.Course):
    color = theme.course_style(course.slug)[0]
    theme.section("Citation viewer", "Open the exact passage, slide or video frame behind any citation.", "visibility", color)
    with ui.element("div").classes("s-card p-3 w-full"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.icon("format_quote").classes("text-xl ml-2").style("color:var(--muted)")
            cite = ui.input(placeholder=f"[@{course.slug}:L05-slides#p12]  or  [@{course.slug}:L05-rec#t=00:20:00]"
                            ).classes("flex-grow font-mono").props("borderless")
            ui.button("Show", icon="arrow_forward", on_click=lambda: render()).props("unelevated color=primary")
    out = ui.column().classes("w-full gap-3")

    def render():
        out.clear()
        _render_citation(show_citation(db(), cite.value or "", context=1), out)
    cite.on("keydown.enter", render)


# ---------------------------------------------------------------- practice session

@ui.page("/practice/{slug}/{session}")
def practice_page(slug: str, session: str):
    import json as _json
    from study.practice.questions import (QuestionError, get, read_answers, run_tests, session_attempts,
                                          session_dir, write_answers)
    course = config.get_course(paths(), slug)
    frame(f"{course.name} · practice", slug)
    try:
        sdir = session_dir(course, session)
    except QuestionError as e:
        theme.empty("error_outline", str(e))
        return
    color = theme.course_style(slug)[0]
    meta = _json.loads((sdir / "session.json").read_text())
    answers = read_answers(sdir)
    graded = session_attempts(db(), session)
    boxes, editors = {}, {}
    with ui.row().classes("w-full items-end justify-between"):
        theme.page_header("Practice session", f"{course.name} · {session} · {len(meta['questions'])} questions · "
                          "answer, then submit for step-by-step grading")
        ui.button("Back to course", icon="arrow_back", on_click=lambda: ui.navigate.to(f"/course/{slug}")).props("flat")
    for i, qid in enumerate(meta["questions"], 1):
        q = get(db(), qid)
        with ui.element("div").classes("s-card p-6 w-full max-w-5xl"):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
                    with ui.element("div").classes("s-iconbox").style(
                            f"background:color-mix(in srgb,{color} 14%,transparent);color:{color};font-weight:800"):
                        ui.label(f"Q{i}")
                    ui.label(q.meta["title"]).classes("text-lg font-bold truncate")
                with ui.row().classes("gap-2 no-wrap"):
                    theme.chip(q.meta["kind"])
                    theme.chip(str(q.meta["difficulty"]))
                    theme.chip(f"{q.meta['points']} pts", "star")
            render_md(q.body)
            if q.is_code:
                for f in sorted((sdir / qid / "src").glob("*")):
                    theme.chip(f.name, "description")
                    editors[f] = ui.codemirror(f.read_text(), language="C++", theme="vscodeDark").classes(
                        "w-full h-72 rounded-xl overflow-hidden")
                out = ui.column().classes("w-full")

                async def test(qid=qid, out=out):
                    save(quiet=True)
                    res = await run.io_bound(run_tests, get(db(), qid), sdir / qid / "src", paths().data / "build")
                    out.clear()
                    with out:
                        theme.pill(("passed · " if res["passed"] else "failed · ") + res["summary"],
                                   theme.GREEN if res["passed"] else theme.RED)
                        detail = (res.get("compile_output") or "") + (res.get("stderr") or "")
                        if detail.strip():
                            ui.code(detail[-4000:]).classes("w-full text-xs")
                ui.button("Run tests", icon="science", on_click=test).props("outline")
            boxes[qid] = ui.textarea("Your answer (Markdown, LaTeX with $…$)", value=answers.get(qid, "")).classes(
                "w-full mt-2").props("autogrow outlined")
            if qid in graded:
                a = graded[qid]
                score = a["score"]
                with ui.row().classes("items-center gap-3 w-full mt-3"):
                    theme.pill(f"Graded {score:.0%}" + (f" · {a['points']:g}/{a['max_points']:g}" if a["points"] is not None
                                                        else ""), theme.level_color(score))
                    with ui.element("div").classes("flex-1"):
                        theme.bar(score, theme.level_color(score))
                fb = Path(a["feedback"]) if a["feedback"] else None
                if fb and fb.exists():
                    with ui.expansion("Feedback", icon="rate_review", value=True).classes("w-full"):
                        render_md(fb.read_text())

    def save(quiet=False):
        write_answers(sdir, {qid: b.value or "" for qid, b in boxes.items()})
        for f, ed in editors.items():
            f.write_text(ed.value)
        if not quiet:
            ui.notify("Saved", type="positive")

    def submit():
        save(quiet=True)
        ui.navigate.to(claude_link(slug, f"/grade {slug} {session}"))
    with ui.element("div").classes("s-card p-3 w-full max-w-5xl sticky bottom-4 z-10"):
        with ui.row().classes("w-full items-center justify-end gap-2"):
            ui.label("Answers are saved to your session folder").classes("text-xs mr-auto ml-2").style("color:var(--muted)")
            ui.button("Save", icon="save", on_click=lambda: save()).props("flat")
            ui.button("Submit for grading", icon="grading", on_click=submit).props("unelevated color=positive")


# ---------------------------------------------------------------- documents

@ui.page("/digest/{slug}/{lecture}")
def digest_page(slug: str, lecture: int):
    from study.digest import digest_path
    course = config.get_course(paths(), slug)
    frame(f"{course.name} · Week {lecture}", slug)
    p = digest_path(course, lecture)
    if not p.exists():
        theme.empty("menu_book", "No digest for this week yet")
        return
    weeks = sorted(int(x.stem[1:]) for x in (course.dir / "digests").glob("L*.md") if x.stem[1:].isdigit())

    def extra():
        with ui.row().classes("gap-1"):
            prev = [w for w in weeks if w < lecture]
            nxt = [w for w in weeks if w > lecture]
            if prev:
                ui.button(f"Week {prev[-1]}", icon="chevron_left",
                          on_click=lambda: ui.navigate.to(f"/digest/{slug}/{prev[-1]}")).props("flat")
            if nxt:
                ui.button(f"Week {nxt[0]}",
                          on_click=lambda: ui.navigate.to(f"/digest/{slug}/{nxt[0]}")).props("flat icon-right=chevron_right")
            ui.button("Ask about this week", icon="auto_awesome", on_click=lambda: ui.navigate.to(
                claude_link(slug, f"/ask {slug} (about week {lecture}) "))).props("unelevated color=primary")
    with _doc_shell(slug, f"{course.name} · week {lecture}", f"/course/{slug}", extra):
        render_md(p.read_text())


@ui.page("/doc/{slug}/{name}")
def course_doc(slug: str, name: str):
    course = config.get_course(paths(), slug)
    if name not in ("glossary", "key_results"):
        theme.empty("help_outline", "Unknown document")
        return
    frame(f"{course.name} · {name.replace('_', ' ')}", slug)
    p = course.dir / f"{name}.md"
    text = p.read_text() if p.exists() else "_Nothing yet._"
    text = re.sub(r"\]\(digests/L(\d+)\.md\)", lambda m: f"](/digest/{slug}/{int(m.group(1))})", text)
    with _doc_shell(slug, f"{course.name} · {name.replace('_', ' ')}", f"/course/{slug}"):
        render_md(text)


@ui.page("/cite")
def cite_page(c: str):
    frame("Citation")
    with ui.row().classes("w-full items-center gap-3"):
        ui.button(icon="arrow_back", on_click=lambda: ui.run_javascript("history.back()")).props("flat round")
        ui.label(c).classes("font-mono text-primary break-all")
    out = ui.column().classes("w-full gap-3")
    _render_citation(show_citation(db(), c, context=1), out, "w-[32rem]")


# ---------------------------------------------------------------- planner

@ui.page("/planner")
def planner_page():
    import datetime as _dt
    from study.plan.planner import PlanError, Settings, build_plan, to_ics, to_markdown
    frame("Planner", active="planner")
    try:
        settings = Settings.load(paths().courses / "planner.yaml")
    except PlanError as e:
        ui.label(str(e)).classes("text-red-600")
        return
    with ui.row().classes("w-full items-end justify-between"):
        theme.page_header("Planner", "Priorities × backlog × exam urgency, spread fairly across your courses. Exam dates are never guessed.")
    with ui.element("div").classes("s-card p-4 w-full"):
        with ui.row().classes("items-end gap-3 w-full"):
            days = ui.number("Days", value=7, min=1, max=60, format="%d").props("outlined dense").classes("w-24")
            start = ui.input("Start", value=_dt.date.today().isoformat()).props("outlined dense").classes("w-40")
            go = ui.button("Build plan", icon="auto_fix_high").props("unelevated color=primary")
            ui.space()
            dl = ui.button("Download .ics", icon="download").props("flat")
            ui.button("Push to my planner", icon="send", on_click=lambda: ui.navigate.to(
                claude_link("", f"/plan days={int(days.value or 7)}"))).props("outline")
    warn = ui.row().classes("gap-2 w-full")
    out = ui.element("div").classes("grid gap-4 w-full").style("grid-template-columns:repeat(auto-fill,minmax(320px,1fr))")

    def render():
        out.clear()
        warn.clear()
        try:
            d0 = _dt.date.fromisoformat(start.value)
        except ValueError:
            ui.notify("Start must be YYYY-MM-DD", type="negative")
            return
        plan = build_plan(db(), courses(), settings, d0, int(days.value or 7))
        pdir = paths().data / "plan"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "plan.ics").write_text(to_ics(plan))
        (pdir / "plan.md").write_text(to_markdown(plan))
        dl.on_click(lambda: ui.download(pdir / "plan.ics", "study-plan.ics"))
        with warn:
            missing = [w.split(":")[0] for w in plan["warnings"] if "no exam date" in w]
            if missing:
                theme.chip(f"Steady mode: no exam dates for {', '.join(missing)}", "info", "#f59e0b")
        by_day: dict[str, list] = {}
        for b in plan["blocks"]:
            by_day.setdefault(b["date"], []).append(b)
        with out:
            for i in range(int(days.value or 7)):
                day = (d0 + _dt.timedelta(days=i)).isoformat()
                d = _dt.date.fromisoformat(day)
                with ui.element("div").classes("s-card p-5"):
                    with ui.row().classes("items-baseline justify-between w-full"):
                        ui.label(d.strftime("%A")).classes("s-h2")
                        ui.label(d.strftime("%d %b") + ("  · winter break" if day in plan["winter_days"] else "")).classes(
                            "text-xs").style("color:var(--muted)")
                    if not by_day.get(day):
                        ui.label("Free day").classes("s-sub mt-2")
                    for b in by_day.get(day, []):
                        color, short, icon = theme.course_style(b["course"]) if b["course"] != "all" else ("#64748b", "ANKI", "style")
                        with ui.element("div").classes("s-timeline-item w-full mt-1").style(f"border-left:3px solid {color}"):
                            with ui.column().classes("gap-0 flex-1 min-w-0"):
                                ui.label(b["title"]).classes("text-sm font-semibold")
                                ui.label(f"{b['start']}–{b['end']} · {short}").classes("text-xs").style("color:var(--muted)")
                            if b["command"].startswith("/"):
                                ui.button(icon="play_arrow", on_click=lambda b=b: ui.navigate.to(
                                    command_link(b["course"] if b["course"] != "all" else "", b["command"]))).props(
                                    "round unelevated size=sm").style(f"background:{color};color:white").tooltip(b["command"])
    go.on_click(render)
    render()


# ---------------------------------------------------------------- visualize

@ui.page("/viz")
def viz_page():
    from study.viz import ExprError, bifurcation_1d, phase_portrait
    frame("Visualize dynamics", active="viz")
    theme.page_header("Visualize dynamics", "Phase portraits with classified fixed points, and 1-D bifurcation diagrams. "
                      "Use x, y (and parameter r); numpy functions like sin, exp work.")
    with ui.tabs().props("dense no-caps inline-label") as tabs:
        tp = ui.tab("Phase portrait", icon="all_inclusive")
        tb = ui.tab("Bifurcation (1-D)", icon="call_split")
    with ui.tab_panels(tabs, value=tp).classes("w-full"):
        with ui.tab_panel(tp):
            with ui.element("div").classes("s-card p-4 w-full"):
                with ui.row().classes("items-end gap-3"):
                    fx = ui.input("x' =", value="y").props("outlined dense").classes("w-64 font-mono")
                    fy = ui.input("y' =", value="-sin(x) - 0.25*y").props("outlined dense").classes("w-64 font-mono")
                    xl = ui.input("x range", value="-7 7").props("outlined dense").classes("w-28")
                    yl = ui.input("y range", value="-3 3").props("outlined dense").classes("w-28")
                    go = ui.button("Draw", icon="insights").props("unelevated color=primary")
            out = ui.row().classes("w-full gap-4 items-start")

            async def draw():
                out.clear()
                try:
                    xlim = tuple(map(float, xl.value.split()))
                    ylim = tuple(map(float, yl.value.split()))
                    import time as _t
                    png = paths().data / "tmp" / "plots" / f"gui_phase_{int(_t.time() * 1000)}.png"
                    res = await run.io_bound(phase_portrait, fx.value, fy.value, xlim, ylim, png)
                except (ExprError, SyntaxError, ValueError) as e:
                    ui.notify(f"bad input: {e}", type="negative")
                    return
                kind_color = {"stable": theme.GREEN, "unstable": theme.RED, "saddle": theme.AMBER}
                with out:
                    with ui.element("div").classes("s-card p-3 flex-1 min-w-[320px] max-w-3xl"):
                        ui.image(png).classes("w-full rounded-xl")
                    with ui.element("div").classes("s-card p-5 w-80"):
                        ui.label(f"Fixed points · {len(res['fixed_points'])}").classes("s-h2 mb-2")
                        for p in res["fixed_points"]:
                            c = next((v for k, v in kind_color.items() if k in p["kind"]), theme.BLUE)
                            with ui.element("div").classes("s-timeline-item w-full").style(f"border-left:3px solid {c}"):
                                with ui.column().classes("gap-0"):
                                    ui.label(f"({p['x']:g}, {p['y']:g})").classes("font-mono text-sm font-semibold")
                                    ui.label(p["kind"]).classes("text-xs").style(f"color:{c}")
                                    ui.label("λ = " + ", ".join(p["eigenvalues"])).classes("text-xs font-mono").style(
                                        "color:var(--muted)")
            go.on_click(draw)
        with ui.tab_panel(tb):
            with ui.element("div").classes("s-card p-4 w-full"):
                with ui.row().classes("items-end gap-3"):
                    f1 = ui.input("x' =", value="r*x - x**3").props("outlined dense").classes("w-64 font-mono")
                    pr = ui.input("r range", value="-2 2").props("outlined dense").classes("w-28")
                    xr = ui.input("x range", value="-3 3").props("outlined dense").classes("w-28")
                    gb = ui.button("Draw", icon="insights").props("unelevated color=primary")
            outb = ui.column().classes("w-full gap-3")

            async def drawb():
                outb.clear()
                try:
                    import time as _t
                    png = paths().data / "tmp" / "plots" / f"gui_bif_{int(_t.time() * 1000)}.png"
                    res = await run.io_bound(bifurcation_1d, f1.value, "r", tuple(map(float, pr.value.split())),
                                             tuple(map(float, xr.value.split())), png)
                except (ExprError, SyntaxError, ValueError) as e:
                    ui.notify(f"bad input: {e}", type="negative")
                    return
                with outb:
                    with ui.element("div").classes("s-card p-3 w-full max-w-3xl"):
                        ui.image(png).classes("w-full rounded-xl")
                    theme.chip(f"number of equilibria changes near r = {res['bifurcation_points']}", "call_split")
            gb.on_click(drawb)


# ---------------------------------------------------------------- tools

@ui.page("/tools")
async def tools_page():
    frame("Tools", active="tools")
    theme.page_header("Environment", "Compilers, sanitizers, GPU, MPI, Python packages and AnkiConnect.")
    spinner = ui.row().classes("items-center gap-2")
    with spinner:
        ui.spinner(size="sm")
        ui.label("Checking…").classes("s-sub")
    from study.tools import check_all
    res = await run.io_bound(check_all)
    spinner.delete()
    ok = sum(1 for v in res.values() if v["ok"])
    with ui.row().classes("w-full gap-4"):
        theme.tile(f"{ok}/{len(res)}", "checks passing", "verified", theme.GREEN)
        theme.tile(str(len(res) - ok), "unavailable (features gated off)", "block", theme.GREY)
    with ui.element("div").classes("grid gap-3 w-full").style("grid-template-columns:repeat(auto-fill,minmax(300px,1fr))"):
        for k, v in res.items():
            c = theme.GREEN if v["ok"] else theme.GREY
            with ui.element("div").classes("s-card p-4"):
                with ui.row().classes("items-center gap-3 no-wrap"):
                    with ui.element("div").classes("s-iconbox").style(
                            f"background:color-mix(in srgb,{c} 14%,transparent);color:{c}"):
                        ui.icon("check_circle" if v["ok"] else "remove_circle_outline").classes("text-xl")
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(k).classes("font-semibold")
                        ui.label(str(v["detail"])).classes("text-xs break-all").style("color:var(--muted)")


# ---------------------------------------------------------------- Claude console

@ui.page("/claude")
def claude_page(course: str | None = None, q: str | None = None, auto: int = 0):
    frame("Ask Claude", course, active="claude")
    if not claude_available():
        ui.label("The `claude` CLI was not found on PATH.").classes("text-red-600")
        return
    theme.page_header("Ask Claude", "Claude Code on your subscription, inside this project: it follows CLAUDE.md, "
                                    "uses the study tools and cites your materials.")
    state = {"session": None, "task": None}
    cs = courses()
    with ui.row().classes("gap-2"):
        for cmd, label in [("/status", "Where am I?"), ("/plan", "Plan my week"), ("/digest", "Digest next weeks"),
                           ("/practice", "Practise"), ("/cards", "Make flashcards"), ("/parse-exams", "Parse past exams")]:
            ui.button(label, on_click=lambda cmd=cmd: prompt.set_value(cmd + (f" {sel.value}" if sel.value and cmd != "/status" else ""))).props(
                "flat no-caps rounded size=sm").style("background:var(--code-bg)")
    with ui.element("div").classes("s-card p-4 w-full"):
        prompt = ui.textarea(placeholder="Ask anything about your courses, or type a command like /ask <course> what is recursive feasibility",
                             value=q or "").classes("w-full").props("autogrow borderless input-class=text-base")
        with ui.row().classes("items-center gap-2 w-full"):
            sel = ui.select({"": "No course context"} | {c.slug: c.name for c in cs.values()}, value=course or "").props(
                "dense outlined").classes("w-72")
            limit = ui.label("").classes("text-xs text-gray-500")
            ui.space()
            new = ui.button("New chat", icon="refresh").props("flat")
            stop = ui.button(icon="stop").props("round flat color=negative")
            go = ui.button("Send", icon="arrow_upward").props("unelevated rounded").style(
                "background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white")
    stop.disable()
    activity = ui.column().classes("w-full text-xs text-gray-500 gap-0")
    with ui.element("div").classes("s-card p-6 w-full") as answer_card:
        answer = ui.markdown("", extras=MD_EXTRAS).classes("w-full digest")
    answer_card.bind_visibility_from(answer, "content", backward=bool)


    async def send():
        text = (prompt.value or "").strip()
        if not text:
            return
        if sel.value and not text.startswith("/"):
            text = f"(Course: {sel.value}.) {text}"
        go.disable()
        stop.enable()
        buf = answer.content + (f"\n\n---\n**You:** {prompt.value}\n\n" if answer.content else "")
        answer.set_content(buf + "_thinking…_")
        prompt.value = ""

        async def consume():
            nonlocal buf
            last = 0.0
            loop = asyncio.get_running_loop()
            async for ev in run_claude(text, paths().root, session_id=state["session"]):
                if ev.kind == "text":
                    buf += ev.text
                    if loop.time() - last > 0.15:
                        answer.set_content(buf)
                        last = loop.time()
                elif ev.kind == "tool":
                    with activity:
                        ui.label(f"→ {ev.text}")
                elif ev.kind == "status" and ev.data:
                    state["session"] = ev.data.get("session_id")
                elif ev.kind == "limit" and ev.data:
                    st = ev.data.get("status", "")
                    limit.set_text(f"usage: {st} ({ev.data.get('rateLimitType', '')})")
                elif ev.kind == "error":
                    buf += f"\n\n**Error:** {ev.text}"
            answer.set_content(buf)

        state["task"] = asyncio.create_task(consume())
        try:
            await state["task"]
        except asyncio.CancelledError:
            answer.set_content(buf + "\n\n_(stopped)_")
        finally:
            go.enable()
            stop.disable()

    def do_stop():
        if state["task"]:
            state["task"].cancel()

    def do_new():
        state["session"] = None
        answer.set_content("")
        activity.clear()

    go.on_click(send)
    if q and auto:
        ui.timer(0.3, send, once=True)
    stop.on_click(do_stop)
    new.on_click(do_new)


def main(port: int = 8090, show: bool = True) -> None:
    global PATHS
    PATHS = config.default_paths()
    ui.run(title="study", port=port, show=show, reload=False, host="127.0.0.1", favicon="🎓", dark=None,
           storage_secret="study-local-gui")


from study.ui import teach_page, week_page  # noqa: E402,F401  (registers /teach, /slide, /week)
