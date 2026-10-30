"""Lecture week page: a short overview first, then follow-up questions answered from the week's material.

The overview is generated once per week (/brief) and only regenerated when the material changes. The
follow-up chat resumes one Claude session per week, and its transcript is kept in data/followups/."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nicegui import ui

from study import config
from study.ui import theme
from study.ui.app import MD_EXTRAS, claude_link, db, frame, linkify_cites, paths, strip_frontmatter
from study.ui.claude_runner import claude_available, run_claude
from study.ui.teach_page import _friendly, _zoom, cited_pictures


def _hist_file(slug: str, n: int) -> Path:
    return paths().data / "followups" / f"{slug}_W{n:02d}.json"


def _load(slug: str, n: int) -> dict:
    try:
        return json.loads(_hist_file(slug, n).read_text())
    except (OSError, json.JSONDecodeError):
        return {"session": None, "messages": []}


def _save(slug: str, n: int, h: dict) -> None:
    f = _hist_file(slug, n)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(h, ensure_ascii=False, indent=1))


def week_url(slug: str, n: int) -> str:
    return f"/week/{slug}/{n}"


@ui.page("/week/{slug}/{n}")
def week_page(slug: str, n: int):
    from study.brief import brief_path, follow_ups, status
    from study.digest import todo
    try:
        course = config.get_course(paths(), slug)
    except config.ConfigError as e:
        ui.label(str(e))
        return
    frame(f"{course.name} · week {n}", slug)
    color, short, icon = theme.course_style(slug)
    if not claude_available():
        theme.empty("error_outline", "The `claude` CLI was not found on PATH")
        return
    weeks = [it.lecture for it in todo(db(), slug)]
    hist = _load(slug, n)
    state = {"task": None}
    ui.add_css(""".s-msg-user{background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white;border-radius:18px 18px 4px 18px;
                   padding:10px 16px;max-width:80%;align-self:flex-end;white-space:pre-wrap}""")

    # ------------------------------------------------ top bar: back, prev/next week
    with ui.row().classes("w-full max-w-4xl mx-auto items-center justify-between"):
        ui.button("Back", icon="arrow_back", on_click=lambda: ui.navigate.to(f"/course/{slug}")).props("flat")
        with ui.row().classes("gap-1"):
            prev = [w for w in weeks if w < n]
            nxt = [w for w in weeks if w > n]
            if prev:
                ui.button(f"Week {prev[-1]}", icon="chevron_left", on_click=lambda: ui.navigate.to(week_url(slug, prev[-1]))).props("flat")
            if nxt:
                ui.button(f"Week {nxt[0]}", on_click=lambda: ui.navigate.to(week_url(slug, nxt[0]))).props("flat icon-right=chevron_right")

    # ------------------------------------------------ overview card
    card = ui.element("div").classes("s-card w-full max-w-4xl mx-auto px-10 py-8").style(f"border-top:4px solid {color}")

    def render_overview():
        card.clear()
        p = brief_path(course, n)
        st = status(db(), slug).get(n, "missing")
        with card:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(f"{short} · week {n} · overview").classes("text-xs font-bold tracking-widest uppercase").style(f"color:{color}")
                if p.exists():
                    with ui.row().classes("gap-1 items-center"):
                        if st == "stale":
                            theme.pill("material changed", theme.BLUE)
                        ui.button(icon="refresh", on_click=lambda: generate(force=True)).props("flat round dense size=sm").tooltip(
                            "Rewrite the overview")
            if not p.exists():
                with ui.column().classes("w-full items-center py-8 gap-2"):
                    ui.icon("auto_stories").classes("text-5xl").style("color:var(--muted)")
                    ui.label("No overview for this week yet").classes("font-semibold")
                    ui.label("Claude reads the slides and the recording and writes a one-screen summary (about 2–4 minutes).").classes("s-sub")
                    ui.button("Write the overview", icon="auto_awesome", on_click=lambda: generate()).props("unelevated color=primary")
                return
            text = p.read_text()
            body = strip_frontmatter(text)
            main = body.split("\n## Go deeper", 1)[0]
            ui.markdown(linkify_cites(main), extras=MD_EXTRAS).classes("w-full digest")
            qs = follow_ups(text)
            if qs:
                with ui.element("div").classes("digest w-full"):
                    ui.html("<h2>Go deeper</h2>")
                ui.label("Click a question to ask it, or type your own below.").classes("text-xs -mt-1 mb-2").style(
                    "color:var(--muted)")
                for q in qs:
                    with ui.element("div").classes("s-timeline-item w-full cursor-pointer").style(
                            f"border-left:3px solid {color}").on("click", lambda q=q: send(q)):
                        ui.markdown(q, extras=MD_EXTRAS).classes("flex-1 min-w-0 text-sm digest")
                        ui.icon("arrow_forward").style(f"color:{color}")


    async def generate(force: bool = False):
        if state["task"]:
            return
        card.clear()
        with card:
            with ui.column().classes("w-full items-center py-10 gap-3"):
                ui.spinner(size="lg")
                msg = ui.label("Reading the slides and the recording…").classes("s-sub")
        prompt = f"/brief {slug} {n}" + (" (the user asked to rewrite it)" if force else "")
        state["task"] = True
        try:
            async for ev in run_claude(prompt, paths().root):
                if ev.kind == "tool":
                    msg.set_text(_friendly(ev.text).replace("working…", "writing the overview…"))
                elif ev.kind == "error":
                    ui.notify(ev.text, type="negative", multi_line=True)
        finally:
            state["task"] = None
        render_overview()

    # ------------------------------------------------ follow-up chat
    ui.label("Follow-up questions").classes("s-h2 w-full max-w-4xl mx-auto mt-2")
    chat = ui.column().classes("w-full max-w-4xl mx-auto gap-4")
    status_row = ui.row().classes("w-full max-w-4xl mx-auto items-center gap-2 text-xs").style("color:var(--muted)")
    with ui.element("div").classes("s-card p-3 w-full max-w-4xl mx-auto sticky bottom-3 z-10").style(
            "background:var(--surface-solid)!important"):
        with ui.row().classes("w-full items-end gap-2 no-wrap"):
            box = ui.textarea(placeholder=f"Ask anything about week {n}: why, how, an example, a derivation, “test me”… "
                                          "(Enter to send)").classes("flex-1").props("autogrow borderless input-class=text-base")
            ui.button(icon="refresh", on_click=lambda: new_chat()).props("round flat").tooltip("New conversation")
            stop = ui.button(icon="stop", on_click=lambda: state["task"] and state["task"].cancel()).props("round flat color=negative")
            go = ui.button(icon="arrow_upward", on_click=lambda: send()).props("round unelevated").style(
                "background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white")
    stop.disable()
    box.on("keydown.enter.exact.prevent", lambda: send())

    def user_bubble(text: str):
        with chat:
            ui.label(text).classes("s-msg-user text-sm")

    def ai_bubble(text: str = ""):
        with chat:
            with ui.element("div").classes("s-card w-full").style("padding:18px 22px;border-radius:18px 18px 18px 4px"):
                md = ui.markdown(linkify_cites(text), extras=MD_EXTRAS).classes("w-full digest")
                pics = ui.row().classes("gap-3 flex-wrap mt-2")
        if text:
            add_pictures(pics, text)
        return md, pics

    def add_pictures(pics, text: str):
        pics.clear()
        with pics:
            for cite, url in cited_pictures(text, limit=3):
                ui.image(url).classes("w-56 rounded-xl cursor-zoom-in s-hover").style("border:1px solid var(--border)").on(
                    "click", lambda u=url: _zoom(u))

    async def send(text: str | None = None):
        text = (text if text is not None else box.value or "").strip()
        if not text or state["task"]:
            return
        box.value = ""
        user_bubble(text)
        hist["messages"].append({"role": "user", "text": text})
        md, pics = ai_bubble()
        md.set_content("_thinking…_")
        go.disable()
        stop.enable()
        buf = ""
        prompt = text if hist.get("session") else f"/followup {slug} {n} {text}"

        async def consume(p, session):
            nonlocal buf
            loop, last, err = asyncio.get_running_loop(), 0.0, None
            async for ev in run_claude(p, paths().root, session_id=session):
                if ev.kind == "text":
                    buf += ev.text
                    if loop.time() - last > 0.15:
                        md.set_content(linkify_cites(buf))
                        last = loop.time()
                elif ev.kind == "tool":
                    buf = ""  # narration before tool calls is not part of the answer
                    md.set_content("_looking it up…_")
                    status_row.clear()
                    with status_row:
                        ui.spinner(size="xs")
                        ui.label(_friendly(ev.text))
                elif ev.kind == "status" and ev.data and ev.data.get("session_id"):
                    hist["session"] = ev.data["session_id"]
                elif ev.kind == "error":
                    err = ev.text
            return err

        async def turn():
            nonlocal buf
            err = await consume(prompt, hist.get("session"))
            if err and not buf.strip() and hist.get("session"):  # session couldn't be resumed: start a new one
                hist["session"] = None
                err = await consume(f"/followup {slug} {n} {text}", None)
            if err:
                buf += f"\n\n**Error:** {err}"
        state["task"] = asyncio.create_task(turn())
        try:
            await state["task"]
        except asyncio.CancelledError:
            buf += "\n\n_(stopped)_"
        finally:
            state["task"] = None
            status_row.clear()
            go.enable()
            stop.disable()
        md.set_content(linkify_cites(buf))
        add_pictures(pics, buf)
        hist["messages"].append({"role": "assistant", "text": buf})
        _save(slug, n, hist)

    def new_chat():
        hist["session"] = None
        hist["messages"] = []
        _save(slug, n, hist)
        chat.clear()

    for m in hist["messages"]:
        (user_bubble if m["role"] == "user" else ai_bubble)(m["text"])
    render_overview()
    if not brief_path(course, n).exists() and not hist["messages"]:
        ui.timer(0.3, lambda: generate(), once=True)


__all__ = ["week_url", "claude_link"]
