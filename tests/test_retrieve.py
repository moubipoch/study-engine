import hashlib

import numpy as np
import pytest

from study.cite import check_text
from study.ingest.pipeline import ingest
from study.retrieve.bundle import bundle
from study.retrieve.embed import embed_missing
from study.retrieve.search import Filters, fts_query, rrf, search


class FakeEmbedder:
    """Bag-of-words hashing embedder: deterministic, no model download."""
    name = "fake-bow"

    def _vec(self, text):
        v = np.zeros(64, dtype=np.float32)
        for tok in text.lower().split():
            v[int(hashlib.md5(tok.strip(".,:;()").encode()).hexdigest(), 16) % 64] += 1
        return v / max(np.linalg.norm(v), 1e-9)

    def embed(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def indexed(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    emb = FakeEmbedder()
    embed_missing(con, emb)
    return con, emb


def test_embeddings_are_cached_by_content(project, materials, indexed):
    con, emb = indexed
    paths, course, _ = project
    assert embed_missing(con, emb) == 0  # nothing new
    ingest(con, paths, course, str(materials), copy=False, force=True)  # re-extract identical content
    assert embed_missing(con, emb) == 0  # same content hashes -> no re-embedding


def test_hybrid_search_returns_valid_citations(indexed):
    con, emb = indexed
    res = search(con, "demo", "rvalue reference temporaries", embedder=emb)
    assert res["results"] and res["mode"] == "hybrid"
    for r in res["results"]:
        _, errors = check_text(con, r["cite"], "demo")
        assert not errors, (r["cite"], errors)
    assert any("rvalue" in r["text"].lower() for r in res["results"][:3])


def test_filters_and_lexical_fallback(indexed):
    con, emb = indexed
    res = search(con, "demo", "move semantics", flt=Filters(types=["recording"]), embedder=emb)
    assert res["results"] and {r["type"] for r in res["results"]} == {"recording"}
    res = search(con, "demo", "Particle step", flt=Filters(key_prefix="ex02/"), mode="lexical")
    assert res["results"][0]["key"] == "ex02/vector.cpp"


def test_fts_query_is_injection_safe(indexed):
    con, _ = indexed
    assert fts_query('") OR 1=1; DROP TABLE x --') == '"OR" OR "1" OR "1" OR "DROP" OR "TABLE"'
    search(con, "demo", 'NEAR( "unbalanced', mode="lexical")  # must not raise an FTS syntax error
    assert fts_query("+++") is None


def test_rrf_prefers_agreement():
    s = rrf([1, 2, 3], [3, 1, 4])
    assert max(s, key=s.get) == 1 and s[3] > s[2] and s[4] > 0


def test_bundle_is_bounded_and_resumable(indexed):
    con, _ = indexed
    full = bundle(con, "demo", lecture=3, max_tokens=100000)
    assert full["total_chunks"] == len(full["chunks"]) and not full["truncated"]
    assert [s["type"] for s in full["sources"]][:2] == ["slides", "recording"]  # reading order
    small = bundle(con, "demo", lecture=3, max_tokens=60)
    assert small["truncated"] and small["chunks"]
    rest = bundle(con, "demo", lecture=3, max_tokens=100000, offset=small["next_offset"])
    assert [c["cite"] for c in small["chunks"] + rest["chunks"]] == [c["cite"] for c in full["chunks"]]
    for c in full["chunks"]:
        assert not check_text(con, c["cite"], "demo")[1]
