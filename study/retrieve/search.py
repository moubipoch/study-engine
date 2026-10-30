"""Hybrid retrieval: SQLite FTS5 BM25 + local embeddings, fused with reciprocal-rank fusion."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from study.cite import make_cite
from study.retrieve.text import compact

RRF_K = 60
POOL = 50  # candidates taken from each retriever before fusion
_TOKEN = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class Filters:
    lecture: int | None = None
    types: list[str] = field(default_factory=list)
    key_prefix: str | None = None

    def sql(self) -> tuple[str, list]:
        where, args = [], []
        if self.lecture is not None:
            where.append("s.lecture=?")
            args.append(self.lecture)
        if self.types:
            where.append(f"s.type IN ({','.join('?' * len(self.types))})")
            args += self.types
        if self.key_prefix:
            where.append("s.key LIKE ? ESCAPE '\\'")
            args.append(self.key_prefix.replace("%", "\\%").replace("_", "\\_") + "%")
        return "".join(f" AND {w}" for w in where), args


def fts_query(q: str) -> str | None:
    """User text -> safe FTS5 query: every token quoted, OR-ed (BM25 rewards matching more of them)."""
    toks = [t for t in _TOKEN.findall(q) if len(t) > 1 or t.isdigit()]
    return " OR ".join(f'"{t}"' for t in toks) or None


def lexical(con: sqlite3.Connection, course: str, q: str, flt: Filters, limit: int = POOL) -> list[int]:
    fq = fts_query(q)
    if not fq:
        return []
    wsql, wargs = flt.sql()
    rows = con.execute(
        "SELECT ch.id FROM chunks_fts JOIN chunks ch ON ch.id=chunks_fts.rowid JOIN sources s ON s.id=ch.source_id"
        f" WHERE chunks_fts MATCH ? AND s.course=?{wsql} ORDER BY bm25(chunks_fts, 1.0, 1.5) LIMIT ?",
        [fq, course, *wargs, limit]).fetchall()
    return [r[0] for r in rows]


def semantic(con: sqlite3.Connection, course: str, qvec: np.ndarray, model: str, flt: Filters,
             limit: int = POOL) -> list[int]:
    wsql, wargs = flt.sql()
    rows = con.execute(
        "SELECT ch.id, e.vec FROM chunks ch JOIN sources s ON s.id=ch.source_id"
        " JOIN embeddings e ON e.content_hash=ch.content_hash AND e.model=?"
        f" WHERE s.course=?{wsql}", [model, course, *wargs]).fetchall()
    if not rows:
        return []
    ids = np.array([r[0] for r in rows])
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    scores = mat @ qvec.astype(np.float32)
    top = np.argsort(-scores)[:limit]
    return [int(ids[i]) for i in top]


def rrf(*rankings: list[int], k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _images_for(con, source_id: int, kind: str, a: float, b: float, n: int = 2) -> list[str]:
    if kind == "t":
        rows = con.execute("SELECT path FROM images WHERE source_id=? AND loc BETWEEN ? AND ? ORDER BY loc",
                           (source_id, a - 5, b)).fetchall()
        paths = [r[0] for r in rows]
        return paths[:: max(1, len(paths) // n)][:n] if paths else []
    if kind == "p":
        return [r[0] for r in con.execute("SELECT path FROM images WHERE source_id=? AND loc BETWEEN ? AND ?",
                                          (source_id, a, b))]
    return []


def hydrate(con: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = con.execute(
        "SELECT ch.id, ch.source_id, ch.loc_start, ch.loc_end, ch.heading, ch.text, s.course, s.key, s.type,"
        f" s.lecture, s.loc_kind FROM chunks ch JOIN sources s ON s.id=ch.source_id WHERE ch.id IN ({','.join('?' * len(ids))})",
        ids).fetchall()
    out = {}
    for r in rows:
        out[r["id"]] = {
            "cite": make_cite(r["course"], r["key"], r["loc_kind"], r["loc_start"], r["loc_end"]),
            "course": r["course"], "key": r["key"], "type": r["type"], "lecture": r["lecture"],
            "loc_kind": r["loc_kind"], "loc_start": r["loc_start"], "loc_end": r["loc_end"],
            "heading": r["heading"], "text": compact(r["text"]),
            "images": _images_for(con, r["source_id"], r["loc_kind"], r["loc_start"], r["loc_end"]),
        }
    return out


def search(con: sqlite3.Connection, course: str, q: str, *, k: int = 8, flt: Filters | None = None,
           mode: str = "hybrid", embedder=None, max_chars: int | None = 1200) -> dict:
    """Return {"results": [...], "mode": used_mode, "warning": str|None}."""
    flt = flt or Filters()
    lex = lexical(con, course, q, flt) if mode in ("hybrid", "lexical") else []
    sem, warning, used = [], None, mode
    if mode in ("hybrid", "semantic"):
        try:
            from study.retrieve.embed import get_embedder
            emb = embedder or get_embedder()
            sem = semantic(con, course, emb.embed_query(q), emb.name, flt)
            if not sem:
                warning = "no embeddings for this course yet (run `study embed`); lexical results only"
                used = "lexical"
        except Exception as e:  # model not downloadable offline, etc.
            warning, used = f"semantic search unavailable ({type(e).__name__}: {e}); lexical results only", "lexical"
    scores = rrf(lex, sem)
    ranked = sorted(scores, key=lambda cid: -scores[cid])[:k]
    info = hydrate(con, ranked)
    lex_rank = {cid: i + 1 for i, cid in enumerate(lex)}
    sem_rank = {cid: i + 1 for i, cid in enumerate(sem)}
    results = []
    for cid in ranked:
        r = info[cid] | {"score": round(scores[cid], 5), "lex_rank": lex_rank.get(cid), "sem_rank": sem_rank.get(cid)}
        if max_chars and len(r["text"]) > max_chars:
            r["text"] = r["text"][:max_chars] + " …"
            r["truncated"] = True
        results.append(r)
    return {"query": q, "mode": used, "warning": warning, "results": results}
