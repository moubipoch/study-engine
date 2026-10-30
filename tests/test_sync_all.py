from study.ingest.pipeline import ingest
from study.sync_all import free_key


def test_free_key_never_reuses_an_existing_source(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False, lecture=3)
    keys = {r[0] for r in con.execute("SELECT key FROM sources WHERE course='demo'")}
    taken = next(k for k in keys if k.startswith("L03"))
    assert free_key(con, "demo", "L09-rec") == "L09-rec"
    assert free_key(con, "demo", taken) != taken and free_key(con, "demo", taken).startswith("L03b")
