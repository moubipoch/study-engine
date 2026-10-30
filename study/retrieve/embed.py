"""Local embeddings. Model: BAAI/bge-small-en-v1.5 via fastembed (ONNX, CPU).

Why this model: 33M params / 384-d / ~70 MB quantized ONNX; top of its size class on retrieval
benchmarks (MTEB); fast on CPU (no GPU or torch needed, so it never competes with Whisper for VRAM);
English, matching the course material. Vectors live in SQLite as float32 BLOBs keyed by chunk content
hash, and search is a brute-force cosine in numpy (well under 10 ms for 10^4–10^5 chunks).
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Protocol

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_CHARS = 2000  # bge truncates at 512 tokens anyway


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    name = MODEL

    def __init__(self, cache_dir: str | None = None):
        from fastembed import TextEmbedding
        self._m = TextEmbedding(MODEL, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._m.embed([t[:MAX_CHARS] for t in texts], batch_size=64)), dtype=np.float32)
        return _normalise(vecs)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([QUERY_PREFIX + text])[0]


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


_override: Embedder | None = None


def set_embedder(e: Embedder | None) -> None:
    """Tests install a deterministic fake here."""
    global _override
    _override = e


@lru_cache(maxsize=1)
def _default(cache_dir: str | None) -> Embedder:
    return FastEmbedder(cache_dir)


def get_embedder(cache_dir: str | None = None) -> Embedder:
    return _override or _default(cache_dir)


def chunk_text(heading: str, text: str) -> str:
    return f"{heading}\n{text}" if heading else text


def embed_missing(con: sqlite3.Connection, embedder: Embedder, course: str | None = None,
                  batch: int = 256, progress=None) -> int:
    """Embed every chunk whose content hash has no vector yet. Returns the number embedded."""
    q = ("SELECT DISTINCT ch.content_hash, ch.heading, ch.text FROM chunks ch JOIN sources s ON s.id=ch.source_id"
         " LEFT JOIN embeddings e ON e.content_hash=ch.content_hash AND e.model=?"
         " WHERE e.content_hash IS NULL")
    args: list = [embedder.name]
    if course:
        q, args = q + " AND s.course=?", args + [course]
    todo = con.execute(q, args).fetchall()
    done = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        vecs = embedder.embed([chunk_text(r["heading"], r["text"]) for r in part])
        with con:
            con.executemany("INSERT OR REPLACE INTO embeddings(content_hash, model, vec) VALUES (?,?,?)",
                            [(r["content_hash"], embedder.name, v.astype(np.float32).tobytes())
                             for r, v in zip(part, vecs)])
        done += len(part)
        if progress:
            progress(done, len(todo))
    return done
