"""Visual design system for the GUI: fonts, colour tokens (light + dark), glass cards, course accents."""

from __future__ import annotations

from nicegui import ui

_PALETTE = ["#6366f1", "#a855f7", "#ec4899", "#f59e0b", "#10b981", "#06b6d4", "#f43f5e", "#3b82f6"]


def course_style(slug: str) -> tuple[str, str, str]:
    """(accent colour, short label, icon): course.yaml `ui: {color, icon}` and `short`, else a palette colour."""
    from study import config
    try:
        c = config.get_course(config.default_paths(), slug)
        ui_cfg, short = c.raw.get("ui") or {}, c.short
    except Exception:
        ui_cfg, short = {}, slug.upper()[:5]
    color = ui_cfg.get("color") or _PALETTE[sum(map(ord, slug)) % len(_PALETTE)]
    return color, short, ui_cfg.get("icon") or "school"


CSS = r"""
:root{
  --bg:#f5f6fb; --bg2:#eef0f8; --surface:rgba(255,255,255,.78); --surface-solid:#ffffff; --border:rgba(15,23,42,.08);
  --text:#0f172a; --muted:#64748b; --accent:#6366f1; --accent2:#06b6d4; --shadow:0 1px 2px rgba(15,23,42,.04),0 8px 24px rgba(15,23,42,.06);
  --glow1:rgba(99,102,241,.16); --glow2:rgba(6,182,212,.12); --code-bg:rgba(15,23,42,.05);
}
body.body--dark{
  --bg:#070b16; --bg2:#0c1222; --surface:rgba(22,28,45,.66); --surface-solid:#111827; --border:rgba(148,163,184,.12);
  --text:#e5e9f5; --muted:#8b95ad; --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 32px rgba(0,0,0,.35);
  --glow1:rgba(99,102,241,.22); --glow2:rgba(6,182,212,.12); --code-bg:rgba(148,163,184,.08);
}
html,body{font-family:'Inter',system-ui,-apple-system,sans-serif;font-feature-settings:'cv11','ss01';-webkit-font-smoothing:antialiased}
body{background:var(--bg)!important;color:var(--text)}
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:radial-gradient(900px 600px at 10% -10%,var(--glow1),transparent 60%),
             radial-gradient(800px 500px at 100% 0%,var(--glow2),transparent 60%),var(--bg)}
code,pre,.font-mono,.q-field__native.font-mono{font-family:'JetBrains Mono',ui-monospace,monospace!important}
.text-gray-500,.text-gray-600,.text-gray-400{color:var(--muted)!important}
.nicegui-content{padding:28px 36px 60px;max-width:1440px;margin:0 auto;gap:20px}
/* header + drawer */
.s-header{background:transparent!important;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border);color:var(--text)!important;box-shadow:none!important}
.s-header .q-btn{color:var(--text)}
.s-drawer{background:var(--surface)!important;backdrop-filter:blur(18px);border-right:1px solid var(--border)!important}
.s-brand{font-weight:800;font-size:1.35rem;letter-spacing:-.03em}
.s-logo{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;color:white;
  background:linear-gradient(135deg,#6366f1,#06b6d4);box-shadow:0 6px 18px rgba(99,102,241,.35)}
.s-nav{display:flex;align-items:center;gap:12px;padding:9px 12px;border-radius:12px;color:var(--text)!important;
  text-decoration:none!important;font-weight:500;font-size:.92rem;transition:background .15s,transform .15s}
.s-nav:hover{background:var(--code-bg)}
.s-nav.active{background:linear-gradient(90deg,rgba(99,102,241,.18),rgba(99,102,241,.04));color:var(--accent)!important}
.s-nav .q-icon{font-size:20px;opacity:.85}
.s-section{font-size:.7rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding:14px 12px 6px}
.s-dot{width:9px;height:9px;border-radius:50%;flex:none}
/* cards + tiles */
.s-card{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:20px!important;
  box-shadow:var(--shadow)!important;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);color:var(--text)}
.q-card{background:var(--surface);border:1px solid var(--border);border-radius:18px;box-shadow:var(--shadow);color:var(--text)}
.s-hover{transition:transform .18s ease,box-shadow .18s ease,border-color .18s}
.s-hover:hover{transform:translateY(-3px);box-shadow:0 18px 40px rgba(15,23,42,.14)!important;border-color:rgba(99,102,241,.35)!important}
.s-tile .s-big{font-size:1.9rem;font-weight:750;letter-spacing:-.03em;line-height:1.1}
.s-tile .s-cap{font-size:.78rem;color:var(--muted);font-weight:500}
.s-iconbox{width:40px;height:40px;border-radius:12px;display:grid;place-items:center}
.s-title{font-size:2rem;font-weight:800;letter-spacing:-.035em;line-height:1.15}
.s-sub{color:var(--muted);font-size:.95rem}
.s-h2{font-size:1.05rem;font-weight:700;letter-spacing:-.01em}
.s-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:.74rem;font-weight:600;
  border:1px solid var(--border);background:var(--code-bg);color:var(--text)}
.s-bar{height:7px;border-radius:999px;background:var(--code-bg);overflow:hidden}
.s-bar>div{height:100%;border-radius:999px}
.s-hero{border-radius:24px;padding:28px 30px;color:white;position:relative;overflow:hidden;box-shadow:var(--shadow)}
.s-hero::after{content:"";position:absolute;right:-60px;top:-60px;width:260px;height:260px;border-radius:50%;background:rgba(255,255,255,.12)}
.s-hero .s-chip{background:rgba(255,255,255,.16);border-color:rgba(255,255,255,.25);color:white}
.s-timeline-item{display:flex;gap:14px;align-items:center;padding:10px 12px;border-radius:14px;transition:background .15s}
.s-timeline-item:hover{background:var(--code-bg)}
/* Quasar components */
.q-tabs{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:4px;backdrop-filter:blur(12px)}
.q-tab{border-radius:12px;text-transform:none!important;font-weight:600;min-height:40px;color:var(--muted)}
.q-tab--active{background:var(--code-bg);color:var(--accent)!important}
.q-tab__indicator{display:none}
.q-tab-panels{background:transparent!important}
.q-tab-panel{padding:20px 2px!important}
.q-table__container{background:var(--surface)!important;border:1px solid var(--border);border-radius:18px;box-shadow:var(--shadow);color:var(--text)}
.q-table thead tr th{font-weight:700;color:var(--muted);text-transform:uppercase;font-size:.7rem;letter-spacing:.08em}
.q-table tbody tr:hover{background:var(--code-bg)}
.q-btn{border-radius:12px;text-transform:none;font-weight:600;letter-spacing:0}
.q-field--outlined .q-field__control,.q-field--filled .q-field__control{border-radius:12px}
.q-badge{border-radius:999px;padding:3px 9px;font-weight:600}
.q-linear-progress{border-radius:999px}
.q-expansion-item{border-radius:14px}
.q-dialog__inner>div{border-radius:22px!important}
.q-dialog .q-card{background:var(--surface-solid)!important;backdrop-filter:none}
.q-dialog__backdrop{backdrop-filter:blur(4px)}
.q-uploader{background:var(--surface)!important;border:1px dashed var(--border)!important;border-radius:18px!important;box-shadow:none!important}
.q-notification{border-radius:14px!important}
/* rendered markdown (digests etc.) */
.digest{line-height:1.7;font-size:.98rem}
.digest h1{font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin:.2em 0 .6em}
.digest h2{font-size:1.3rem;font-weight:750;margin:1.6em 0 .5em;padding-bottom:.3em;border-bottom:1px solid var(--border)}
.digest h3{font-size:1.08rem;font-weight:700;margin:1.2em 0 .3em}
.digest p{margin:.5em 0} .digest ul,.digest ol{padding-left:1.4em;margin:.4em 0}
.digest ul{list-style:disc} .digest ol{list-style:decimal}
.digest pre{padding:.9em 1em;border-radius:14px;background:var(--code-bg);overflow-x:auto;border:1px solid var(--border)}
.digest code{background:var(--code-bg);padding:.1em .35em;border-radius:6px;font-size:.88em}
.digest pre code{background:none;padding:0}
.digest a{color:var(--accent);text-decoration:none;font-weight:500;border-bottom:1px dashed rgba(99,102,241,.4)}
.digest strong{font-weight:700}
::-webkit-scrollbar{width:10px;height:10px} ::-webkit-scrollbar-thumb{background:rgba(148,163,184,.3);border-radius:10px}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">')


def apply() -> None:
    ui.add_head_html(FONTS)
    ui.add_css(CSS)
    ui.colors(primary="#6366f1", secondary="#06b6d4", accent="#a855f7", positive="#10b981", negative="#f43f5e",
              warning="#f59e0b", info="#3b82f6")


def bar(value: float | None, color: str) -> None:
    """Thin rounded progress bar (0..1)."""
    v = max(0.0, min(1.0, value or 0.0))
    with ui.element("div").classes("s-bar w-full"):
        ui.element("div").style(f"width:{v * 100:.0f}%;background:{color}")


def chip(text: str, icon: str | None = None, color: str | None = None) -> None:
    with ui.element("span").classes("s-chip").style(f"color:{color}" if color else ""):
        if icon:
            ui.icon(icon).classes("text-sm")
        ui.label(text)


def tile(value: str, caption: str, icon: str, color: str) -> None:
    with ui.element("div").classes("s-card s-tile p-5 flex-1 min-w-[150px]"):
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label(value).classes("s-big")
                ui.label(caption).classes("s-cap")
            with ui.element("div").classes("s-iconbox").style(f"background:{color}22;color:{color}"):
                ui.icon(icon).classes("text-xl")


def page_header(title: str, subtitle: str = "") -> None:
    with ui.column().classes("gap-1"):
        ui.label(title).classes("s-title")
        if subtitle:
            ui.label(subtitle).classes("s-sub")


def section(title: str, subtitle: str = "", icon: str | None = None, color: str = "var(--accent)"):
    """Heading row for a panel section; returns the right-hand row so callers can add buttons."""
    with ui.row().classes("w-full items-center justify-between gap-3 mt-2"):
        with ui.row().classes("items-center gap-3 no-wrap"):
            if icon:
                with ui.element("div").classes("s-iconbox").style(f"background:color-mix(in srgb,{color} 14%,transparent);color:{color}"):
                    ui.icon(icon).classes("text-xl")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("s-h2")
                if subtitle:
                    ui.label(subtitle).classes("text-xs").style("color:var(--muted)")
        right = ui.row().classes("items-center gap-2")
    return right


def empty(icon: str, text: str, hint: str = "") -> None:
    with ui.column().classes("w-full items-center py-10 gap-1 s-card"):
        ui.icon(icon).classes("text-5xl").style("color:var(--muted)")
        ui.label(text).classes("font-semibold mt-1")
        if hint:
            ui.label(hint).classes("s-sub text-center max-w-xl")


def pill(text: str, color: str) -> None:
    """Small solid-tinted status pill (e.g. fresh/stale/missing)."""
    ui.label(text).classes("s-chip").style(
        f"background:color-mix(in srgb,{color} 16%,transparent);color:{color};border-color:color-mix(in srgb,{color} 30%,transparent)")


GREEN, AMBER, RED, BLUE, GREY = "#10b981", "#f59e0b", "#f43f5e", "#3b82f6", "#94a3b8"


def level_color(v: float | None) -> str:
    return GREY if v is None else RED if v < 0.5 else AMBER if v < 0.75 else GREEN
