"""Interactive lesson page (/teach): a tutoring chat with Claude next to the lesson plan.

Each turn runs Claude Code headless and resumes the same Claude session, so the tutor keeps the
conversation. The chat transcript is kept in data/teach/ so a lesson can be reopened days later; the
lesson state itself (plan, position, struggles, checks) lives in the DB via `study teach`.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app, ui

from study import config
from study.cite import find_cites
from study.ui import theme
from study.ui.app import MD_EXTRAS, db, frame, img_url, linkify_cites, paths
from study.ui.claude_runner import claude_available, run_claude

QUICK = [("Continue", "arrow_forward", "Continue."),
         ("Explain differently", "autorenew", "I don't get it yet. Explain it differently, with a simpler example."),
         ("Another example", "lightbulb", "Give me another worked example of this."),
         ("Go deeper", "south", "Go deeper: why is it like this?"),
         ("Show the slide", "slideshow", "Show me the slide for this and walk me through it."),
         ("Quiz me", "quiz", "Quiz me on what we've covered so far."),
         ("I know this", "fast_forward", "I think I know this already. Test me and skip ahead if I'm right.")]


def teach_url(slug: str, target: str) -> str:
    from study.teach import norm_target
    t = norm_target(target)
    return f"/teach/{slug}/{t}" + (f"?label={quote(target)}" if t.startswith("topic-") else "")


def _history_file(slug: str, target: str) -> Path:
    return paths().data / "teach" / f"{slug}_{target}.json"


def _load(slug: str, target: str) -> dict:
    f = _history_file(slug, target)
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {"session": None, "label": None, "messages": []}


def _save(slug: str, target: str, h: dict) -> None:
    f = _history_file(slug, target)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(h, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- cited slides / frames as pictures

@app.get("/slide")
def slide(course: str, key: str, p: int):
    """Render one page of an ingested PDF (any page, not only the pre-rasterized ones)."""
    import pymupdf
    row = db().execute("SELECT path, stored_path FROM sources WHERE course=? AND key=? AND loc_kind='p'",
                       (course, key)).fetchone()
    if not row:
        raise HTTPException(404)
    src = Path(row["stored_path"] or row["path"])
    if src.suffix.lower() != ".pdf" or not src.is_file():
        raise HTTPException(404)
    out = paths().cache / "slides" / course / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}_p{p}.png"
    if not out.exists():
        doc = pymupdf.open(src)
        if not 1 <= p <= doc.page_count:
            raise HTTPException(404)
        out.parent.mkdir(parents=True, exist_ok=True)
        doc[p - 1].get_pixmap(dpi=110).save(out)
    return FileResponse(out)


def cited_pictures(text: str, limit: int = 4) -> list[tuple[str, str]]:
    """(citation, image url) for the slides / video frames a message cites, in order, without duplicates."""
    con = db()
    out, seen = [], set()
    for c in find_cites(text):
        if c.kind == "p":
            ident = (c.course, c.key, int(c.start))
            if ident in seen:
                continue
            seen.add(ident)
            row = con.execute("SELECT path, stored_path FROM sources WHERE course=? AND key=?", (c.course, c.key)).fetchone()
            if row and str(row["stored_path"] or row["path"]).lower().endswith(".pdf"):
                out.append((c.raw, f"/slide?course={quote(c.course)}&key={quote(c.key)}&p={int(c.start)}"))
        elif c.kind == "t":
            r = con.execute("SELECT i.path FROM images i JOIN sources s ON s.id=i.source_id WHERE s.course=? AND s.key=?"
                            " AND i.loc<=? ORDER BY i.loc DESC LIMIT 1", (c.course, c.key, c.start + 5)).fetchone()
            if r and r["path"] not in seen:
                seen.add(r["path"])
                out.append((c.raw, img_url(r["path"])))
        if len(out) >= limit:
            break
    return out


def _zoom(url: str):
    with ui.dialog().props("maximized") as d, ui.card().classes("items-center").style("background:rgba(0,0,0,.88)"):
        ui.button(icon="close", on_click=d.close).props("flat round color=white").classes("self-end")
        ui.image(url).classes("max-w-full").props("fit=contain")
    d.open()


# ---------------------------------------------------------------- page

@ui.page("/teach/{slug}/{target}")
def teach_page(slug: str, target: str, label: str | None = None):
    from study.teach import get, norm_target, set_claude_session, week_of
    try:
        course = config.get_course(paths(), slug)
    except config.ConfigError as e:
        ui.label(str(e))
        return
    target = norm_target(target)
    week = week_of(target)
    hist = _load(slug, target)
    label = label or hist.get("label") or (target[6:].replace("-", " ") if target.startswith("topic-") else None)
    hist["label"] = label
    what = f"week {week}" if week is not None else f'"{label}"'
    frame(f"{course.name} · lesson {what}", slug)
    color, short, icon = theme.course_style(slug)
    if not claude_available():
        theme.empty("error_outline", "The `claude` CLI was not found on PATH", "Install Claude Code to use lessons.")
        return
    ui.add_css(""".s-msg-user{background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white;border-radius:18px 18px 4px 18px;
                   padding:10px 16px;max-width:80%;align-self:flex-end;white-space:pre-wrap}
                  .s-msg-ai{border-radius:18px 18px 18px 4px!important;padding:18px 22px;width:100%}""")
    state = {"task": None}

    with ui.row().classes("w-full gap-5 items-start no-wrap max-lg:flex-wrap"):
        # ------------------------------------------------ chat
        with ui.column().classes("flex-1 min-w-0 gap-4"):
            chat = ui.column().classes("w-full gap-4")
            status = ui.row().classes("items-center gap-2 text-xs").style("color:var(--muted)")
            with ui.element("div").classes("s-card p-3 w-full sticky bottom-3 z-10").style("background:var(--surface-solid)!important"):
                with ui.row().classes("gap-1 flex-wrap mb-1"):
                    quick_btns = []
                    for text, qicon, msg in QUICK:
                        quick_btns.append(ui.button(text, icon=qicon, on_click=lambda m=msg: send(m)).props(
                            "flat no-caps dense rounded size=sm").classes("px-2").style("background:var(--code-bg)"))
                with ui.row().classes("w-full items-end gap-2 no-wrap"):
                    box = ui.textarea(placeholder="Answer the check question, or ask anything… (Enter to send, Shift+Enter for a new line)"
                                      ).classes("flex-1").props("autogrow borderless input-class=text-base")
                    stop = ui.button(icon="stop").props("round flat color=negative")
                    go = ui.button(icon="arrow_upward").props("round unelevated").style(
                        "background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white")
            stop.disable()
        # ------------------------------------------------ lesson plan
        side = ui.column().classes("w-80 max-lg:w-full gap-4 sticky top-20")

    def render_side():
        side.clear()
        s = get(db(), slug, target)
        with side:
            with ui.element("div").classes("s-card p-5 w-full").style(f"border-top:3px solid {color}"):
                ui.label(f"{short} · lesson").classes("text-xs font-bold tracking-widest uppercase").style(f"color:{color}")
                ui.label(s["title"] if s else (f"Week {week}" if week is not None else label)).classes("s-h2 leading-tight mt-1")
                if not s:
                    ui.label("Claude is reading the material and planning the lesson…" if not hist["messages"] else
                             "No plan saved yet.").classes("text-xs mt-2").style("color:var(--muted)")
                    return
                with ui.row().classes("w-full justify-between text-xs mt-3").style("color:var(--muted)"):
                    ui.label("done" if s["status"] == "done" else f"concept {min(s['position'] + 1, s['total'])} of {s['total']}")
                    ui.label(f"{s['position']}/{s['total']}")
                theme.bar(s["position"] / s["total"] if s["total"] else 0, color)
                with ui.column().classes("w-full gap-0 mt-3"):
                    for i, c in enumerate(s["plan"]):
                        done, cur = i < s["position"], i == s["position"] and s["status"] != "done"
                        with ui.row().classes("items-start gap-2 no-wrap py-1"):
                            ui.icon("check_circle" if done else "radio_button_checked" if cur else "radio_button_unchecked"
                                    ).classes("text-base mt-[2px]").style(
                                f"color:{theme.GREEN if done else color if cur else 'var(--muted)'}")
                            ui.label(c["name"]).classes("text-sm" + (" font-semibold" if cur else "")).style(
                                "" if done or cur else "color:var(--muted)")
            checks = s["checks"]
            if checks:
                avg = sum(c["score"] for c in checks) / len(checks)
                with ui.row().classes("w-full gap-3 no-wrap"):
                    theme.tile(f"{avg:.0%}", f"{len(checks)} check answers", "fact_check", theme.level_color(avg))
            if s["struggles"]:
                with ui.element("div").classes("s-card p-4 w-full"):
                    ui.label("To revisit").classes("text-sm font-semibold mb-1")
                    for st in s["struggles"][-5:]:
                        with ui.element("div").classes("s-timeline-item w-full").style(f"border-left:3px solid {theme.AMBER}"):
                            with ui.column().classes("gap-0 min-w-0"):
                                ui.label(st["concept"]).classes("text-xs font-semibold")
                                ui.label(st["note"]).classes("text-xs").style("color:var(--muted)")
            with ui.row().classes("gap-2"):
                ui.button("Back to course", icon="arrow_back", on_click=lambda: ui.navigate.to(f"/course/{slug}")).props(
                    "flat dense no-caps")
                ui.button("Fresh chat", icon="refresh", on_click=fresh_chat).props("flat dense no-caps").tooltip(
                    "Start a new conversation; your progress in the lesson is kept")

    def user_bubble(text: str):
        with chat:
            ui.label(text).classes("s-msg-user text-sm")

    def ai_bubble(text: str = ""):
        with chat:
            card = ui.element("div").classes("s-card s-msg-ai")
            with card:
                md = ui.markdown(linkify_cites(text), extras=MD_EXTRAS).classes("w-full digest")
                pics = ui.row().classes("gap-3 flex-wrap mt-2")
        if text:
            add_pictures(pics, text)
        return md, pics

    def add_pictures(pics, text: str):
        pics.clear()
        with pics:
            for cite, url in cited_pictures(text):
                with ui.column().classes("gap-1 items-center"):
                    ui.image(url).classes("w-64 rounded-xl cursor-zoom-in s-hover").style(
                        "border:1px solid var(--border)").on("click", lambda u=url: _zoom(u))
                    ui.label(re.sub(r"^\[@[^:]+:|\]$", "", cite)).classes("text-[10px] font-mono").style("color:var(--muted)")

    def busy(on: bool):
        for b in [go, *quick_btns]:
            b.set_enabled(not on)
        stop.set_enabled(on)

    async def send(text: str | None = None, *, first: bool = False):
        text = (text if text is not None else box.value or "").strip()
        if not text or state["task"]:
            return
        box.value = ""
        arg = str(week) if week is not None else f'"{label}"'
        if first:
            prompt = f"/teach {slug} {arg}"
        elif hist.get("session"):
            prompt = text
        else:  # no Claude session to resume (fresh chat): the command reloads the lesson state from the DB
            prompt = f"/teach {slug} {arg}\n\n(Resuming the lesson in a new chat. The user says: {text})"
        if not first:
            user_bubble(text)
            hist["messages"].append({"role": "user", "text": text})
        md, pics = ai_bubble()
        md.set_content("_thinking…_")
        busy(True)
        buf = ""

        async def consume(p: str, session: str | None):
            nonlocal buf
            loop = asyncio.get_running_loop()
            last, failed = 0.0, None
            async for ev in run_claude(p, paths().root, session_id=session):
                if ev.kind == "text":
                    buf += ev.text
                    if loop.time() - last > 0.15:
                        md.set_content(linkify_cites(buf))
                        last = loop.time()
                elif ev.kind == "tool":
                    buf = ""  # text before a tool call is Claude narrating its work; the lesson is the final message
                    md.set_content("_preparing…_")
                    status.clear()
                    with status:
                        ui.spinner(size="xs")
                        ui.label(_friendly(ev.text))
                elif ev.kind == "status" and ev.data and ev.data.get("session_id"):
                    hist["session"] = ev.data["session_id"]
                elif ev.kind == "error":
                    failed = ev.text
            return failed

        async def turn():
            nonlocal buf
            err = await consume(prompt, None if first or not hist.get("session") else hist["session"])
            if err and not buf.strip() and hist.get("session") and not first:  # the old session couldn't be resumed
                hist["session"] = None
                err = await consume(f"/teach {slug} {arg}\n\n(Resuming the lesson in a new chat. The user says: {text})", None)
            if err:
                buf += f"\n\n**Error:** {err}"

        state["task"] = asyncio.create_task(turn())
        try:
            await state["task"]
        except asyncio.CancelledError:
            buf += "\n\n_(stopped)_"
        finally:
            state["task"] = None
            status.clear()
            busy(False)
        md.set_content(linkify_cites(buf))
        add_pictures(pics, buf)
        hist["messages"].append({"role": "assistant", "text": buf})
        _save(slug, target, hist)
        if hist.get("session") and get(db(), slug, target):
            set_claude_session(db(), slug, target, hist["session"])
        render_side()

    def fresh_chat():
        hist["session"] = None
        hist["messages"].append({"role": "note", "text": "New chat: Claude reloads the lesson from your saved progress."})
        _save(slug, target, hist)
        with chat:
            ui.label("New chat: Claude reloads the lesson from your saved progress.").classes("text-xs self-center").style(
                "color:var(--muted)")

    def do_stop():
        if state["task"]:
            state["task"].cancel()

    go.on_click(lambda: send())
    stop.on_click(do_stop)
    box.on("keydown.enter.exact.prevent", lambda: send())

    for m in hist["messages"]:
        if m["role"] == "user":
            user_bubble(m["text"])
        elif m["role"] == "assistant":
            ai_bubble(m["text"])
        else:
            with chat:
                ui.label(m["text"]).classes("text-xs self-center").style("color:var(--muted)")
    render_side()
    if not hist["messages"]:
        ui.timer(0.3, lambda: send("start", first=True), once=True)
    else:
        ui.run_javascript("window.scrollTo(0, document.body.scrollHeight)")


def _friendly(tool: str) -> str:
    """Turn a tool call into a short human status line."""
    t = tool.lower()
    for needle, msg in [("study bundle", "reading the lecture material…"), ("study search", "searching your materials…"),
                        ("study teach plan", "saving the lesson plan…"), ("study teach check", "noting your answer…"),
                        ("study teach progress", "updating your progress…"), ("study teach show", "loading your progress…"),
                        ("run-cpp", "compiling and running the example…"), ("run-py", "running the example…"),
                        ("study viz", "drawing a plot…"), ("cards add", "saving flashcards…"), ("read:", "looking at a slide…")]:
        if needle in t:
            return msg
    return "working…"
