"""Token-bounded context packs: everything about one lecture (or one source), in reading order."""

from __future__ import annotations

import sqlite3

from study.cite import make_cite
from study.retrieve.search import _images_for
from study.retrieve.text import compact


TYPE_ORDER = ["slides", "script", "recording", "exercise", "code", "note", "exam"]
CHARS_PER_TOKEN = 4


def bundle(con: sqlite3.Connection, course: str, *, lecture: int | None = None, key: str | None = None,
           types: list[str] | None = None, max_tokens: int = 30000, offset: int = 0,
           headings_only: bool = False) -> dict:
    if lecture is None and key is None and not headings_only:
        raise ValueError("give a lecture number or a source key (or use headings-only for a whole-course outline)")
    q, args = "SELECT * FROM sources WHERE course=?", [course]
    if key is not None:
        q, args = q + " AND key=?", args + [key]
    if lecture is not None:
        q, args = q + " AND lecture=?", args + [lecture]
    if types:
        q, args = q + f" AND type IN ({','.join('?' * len(types))})", args + list(types)
    srcs = sorted(con.execute(q, args).fetchall(),
                  key=lambda s: (s["lecture"] if s["lecture"] is not None else 999,
                                 TYPE_ORDER.index(s["type"]) if s["type"] in TYPE_ORDER else 99, s["key"]))

    items, sources = [], []
    for s in srcs:
        rows = con.execute("SELECT * FROM chunks WHERE source_id=? ORDER BY ord", (s["id"],)).fetchall()
        sources.append({"key": s["key"], "type": s["type"], "loc_kind": s["loc_kind"], "extent": s["extent"],
                        "chunks": len(rows)})
        for r in rows:
            items.append((s, r))

    budget = max_tokens * CHARS_PER_TOKEN
    used, out = 0, []
    i = offset
    while i < len(items):
        s, r = items[i]
        cite = make_cite(course, s["key"], s["loc_kind"], r["loc_start"], r["loc_end"])
        if headings_only:
            entry = {"cite": cite, "heading": r["heading"] or compact(r["text"])[:80]}
            if out and out[-1]["heading"] == entry["heading"]:  # split page / multi-page slide: keep the first
                i += 1
                continue
            cost = len(cite) + len(entry["heading"]) + 10
        else:
            text = compact(r["text"])
            entry = {"cite": cite, "key": s["key"], "type": s["type"], "heading": r["heading"], "text": text,
                     "images": _images_for(con, s["id"], s["loc_kind"], r["loc_start"], r["loc_end"])}
            cost = len(text) + len(r["heading"]) + 80
        if out and used + cost > budget:
            break
        out.append(entry)
        used += cost
        i += 1
    return {
        "course": course, "lecture": lecture, "key": key, "sources": sources, "chunks": out,
        "approx_tokens": used // CHARS_PER_TOKEN, "total_chunks": len(items), "offset": offset,
        "truncated": i < len(items), "next_offset": i if i < len(items) else None,
    }
