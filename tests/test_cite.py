from study.cite import check_text, find_cites, make_cite, resolve
from study.ingest.pipeline import ingest


def test_valid_and_invalid_citations(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    good = ("Moves [@demo:L03-slides#p2] and [@demo:L03-rec#t=00:01:35] and "
            "[@demo:ex02/vector.cpp#L5-10] and [@demo:note-notes#L1-3].")
    cites, errors = check_text(con, good, course="demo")
    assert len(cites) == 4 and errors == []

    bad = {
        "[@demo:L03-slides#p4]": "out of range",          # only 3 pages
        "[@demo:L03-rec#t=00:05:00]": "out of range",     # 130 s long
        "[@demo:ex02/vector.cpp#L20-99]": "out of range",
        "[@demo:nonexistent#p1]": "unknown source",
        "[@demo:L03-slides#t=00:00:10]": "located by",    # wrong location kind
        "[@demo:L03-slides#page3]": "malformed",
        "[@other:L03-slides#p1]": "expected 'demo'",
        "[@demo:ex02/vector.cpp#L9-5]": "reversed",
        "[@demo L03-slides p1]": "malformed citation",
    }
    for cite, reason in bad.items():
        _, errors = check_text(con, f"text {cite} text", course="demo")
        assert errors and reason in errors[0].reason, (cite, errors)


def test_make_cite_roundtrip_and_resolve(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    for args in [("t", 95), ("p", 2, 2), ("L", 5, 10)]:
        key = {"t": "L03-rec", "p": "L03-slides", "L": "ex02/vector.cpp"}[args[0]]
        raw = make_cite("demo", key, *args)
        [c] = find_cites(raw)
        assert c.raw == raw
        _, errors = check_text(con, raw)
        assert not errors
        assert resolve(con, c), raw
    [c] = find_cites("[@demo:L03-rec#t=00:01:35]")
    assert any("rvalue" in r["text"] or "cast" in r["text"] for r in resolve(con, c))
