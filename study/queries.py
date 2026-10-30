"""Read-side queries shared by the CLI and the GUI."""

from __future__ import annotations

import json
import sqlite3

from study.cite import check_cite, find_cites, resolve


def list_sources(con: sqlite3.Connection, course: str, lecture: int | None = None, typ: str | None = None) -> list[dict]:
    q = ("SELECT s.key, s.type, s.lecture, s.loc_kind, s.extent, s.path, s.meta, s.ingested_at,"
         " (SELECT count(*) FROM chunks WHERE source_id=s.id) AS chunks,"
         " (SELECT count(*) FROM images WHERE source_id=s.id) AS images FROM sources s WHERE course=?")
    args: list = [course]
    if lecture is not None:
        q, args = q + " AND lecture=?", args + [lecture]
    if typ:
        q, args = q + " AND type=?", args + [typ]
    return [dict(r) | {"meta": json.loads(r["meta"])} for r in con.execute(q + " ORDER BY lecture, type, key", args)]


def course_stats(con: sqlite3.Connection) -> dict[str, dict]:
    rows = con.execute("SELECT course, count(*) AS sources, sum(type='recording') AS recordings,"
                       " max(ingested_at) AS last FROM sources GROUP BY course").fetchall()
    return {r["course"]: dict(r) for r in rows}


def show_citation(con: sqlite3.Connection, citation: str, context: int = 0) -> dict:
    """{'cite', 'chunks': [...], 'images': [...]} or {'error': reason}."""
    cs = find_cites(citation if citation.strip().startswith("[") else f"[{citation.strip()}]")
    if not cs:
        return {"error": "Not a citation: expected [@course:key#loc]"}
    c = cs[0]
    if err := check_cite(con, c):
        return {"error": err}
    rows = resolve(con, c, context)
    lo, hi = (c.start - 30, c.end + 60) if c.kind == "t" else (c.start, c.end)
    imgs = [r["path"] for r in con.execute(
        "SELECT i.path FROM images i JOIN sources s ON s.id=i.source_id WHERE s.course=? AND s.key=?"
        " AND i.loc BETWEEN ? AND ? ORDER BY i.loc", (c.course, c.key, lo, hi))]
    return {"cite": c.raw, "kind": c.kind,
            "chunks": [{"loc_start": r["loc_start"], "loc_end": r["loc_end"], "heading": r["heading"],
                        "text": r["text"]} for r in rows], "images": imgs}


def source_chunks(con: sqlite3.Connection, course: str, key: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT ch.ord, ch.loc_start, ch.loc_end, ch.heading, ch.text FROM chunks ch JOIN sources s"
        " ON s.id=ch.source_id WHERE s.course=? AND s.key=? ORDER BY ch.ord", (course, key))]


def source_images(con: sqlite3.Connection, course: str, key: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT i.loc, i.kind, i.path FROM images i JOIN sources s ON s.id=i.source_id"
        " WHERE s.course=? AND s.key=? ORDER BY i.loc", (course, key))]
