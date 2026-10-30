from study.ingest.code import extract_code
from study.ingest.media import chunk_segments
from study.ingest.pipeline import default_key, detect_lecture, detect_type, ingest


def _snapshot(con):
    return {
        "sources": con.execute("SELECT course,key,type,sha256,extent FROM sources ORDER BY key").fetchall(),
        "chunks": con.execute("SELECT s.key, c.ord, c.content_hash FROM chunks c JOIN sources s ON s.id=c.source_id"
                              " ORDER BY s.key, c.ord").fetchall(),
        "fts": con.execute("SELECT count(*) FROM chunks_fts").fetchone()[0],
        "images": con.execute("SELECT count(*) FROM images").fetchone()[0],
    }


def test_ingest_is_idempotent(project, materials):
    paths, course, con = project
    first = ingest(con, paths, course, str(materials), copy=False)
    assert {r.status for r in first} == {"added"}, first
    snap = _snapshot(con)
    assert snap["chunks"] and snap["fts"] == len(snap["chunks"])

    again = ingest(con, paths, course, str(materials), copy=False)
    assert {r.status for r in again} == {"unchanged"}
    assert _snapshot(con) == snap


def test_changed_file_replaces_only_its_chunks(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    before = _snapshot(con)
    f = materials / "ex02" / "vector.cpp"
    f.write_text(f.read_text() + "\nint extra() { return 42; }\n")
    res = {r.key: r.status for r in ingest(con, paths, course, str(materials), copy=False)}
    assert res["ex02/vector.cpp"] == "updated"
    assert sum(s == "unchanged" for s in res.values()) == len(res) - 1
    after = _snapshot(con)
    other = lambda snap: [c for c in snap["chunks"] if c[0] != "ex02/vector.cpp"]
    assert other(after) == other(before)
    assert after["fts"] == len(after["chunks"])
    # no orphan FTS rows: searching for the new code finds exactly one chunk
    assert con.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'extra'").fetchone()[0] == 1


def test_duplicate_content_under_new_name_is_skipped(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials / "lecture03_slides.pdf"), copy=False)
    dup = materials / "copy_of_slides.pdf"
    dup.write_bytes((materials / "lecture03_slides.pdf").read_bytes())
    [r] = ingest(con, paths, course, str(dup), copy=False)
    assert r.status == "duplicate"


def test_keys_types_and_locations(project, materials):
    paths, course, con = project
    res = {r.key: r for r in ingest(con, paths, course, str(materials), copy=False)}
    assert set(res) == {"L03-slides", "L03-rec", "ex02/vector.cpp", "note-notes"}
    assert res["L03-rec"].type == "recording"
    rows = {r["key"]: r for r in con.execute("SELECT * FROM sources")}
    assert rows["L03-slides"]["loc_kind"] == "p" and rows["L03-slides"]["extent"] == 3
    assert rows["L03-rec"]["loc_kind"] == "t" and rows["L03-rec"]["extent"] == 130
    assert rows["ex02/vector.cpp"]["loc_kind"] == "L" and rows["ex02/vector.cpp"]["lecture"] is None


def test_cpp_units(tmp_path, materials):
    ex = extract_code(materials / "ex02" / "vector.cpp", "ex02/vector.cpp")
    heads = [c.heading for c in ex.chunks]
    assert any("template sum" in h for h in heads), heads
    assert any("class Particle" in h for h in heads)
    assert any("main()" in h for h in heads)
    # every line with content is covered and ranges are within the file
    for c in ex.chunks:
        assert 1 <= c.loc_start <= c.loc_end <= ex.extent


def test_detection_helpers(project, tmp_path):
    _, course, _ = project
    assert detect_lecture("Lecture_07_templates") == 7
    assert detect_lecture("L03") == 3 and detect_lecture("hw2") is None
    p = tmp_path / "exercise_sheet_04.pdf"
    assert detect_type(p, course) == "exercise"
    assert default_key(p, "exercise", None) == "ex04-sheet"
    assert default_key(tmp_path / "x.mp4", "recording", 5) == "L05-rec"


def test_chunk_segments_window_and_overlap():
    segs = [[i * 10.0, i * 10.0 + 10, f"s{i}"] for i in range(30)]  # 300 s of 10 s segments
    chunks = chunk_segments(segs, window=75, overlap=10)
    assert chunks[0].loc_start == 0 and chunks[-1].loc_end == 300
    for a, b in zip(chunks, chunks[1:]):
        assert b.loc_start < a.loc_end  # overlap
        assert b.loc_start > a.loc_start  # progress
        assert a.loc_end - a.loc_start <= 75


def test_real_world_names(project, tmp_path):
    from study.ingest.pipeline import lecture_tag
    _, course, _ = project
    assert detect_lecture("week00") == 0 and lecture_tag("week01a", 1) == "L01a"
    assert default_key(tmp_path / "week01b.pdf", "slides", 1) == "L01b-slides"
    assert default_key(tmp_path / "exercise03_discussion_solutions.pdf", "exercise", None) == "exercise03-discussion-solutions"
    assert default_key(tmp_path / "SIM_ex01_presentation.pdf", "exercise", None) == "sim-ex01-presentation"
    assert default_key(tmp_path / "sheet_4_solution.pdf", "exercise", None) == "ex04-sheet-solution"
    assert detect_type(tmp_path / "PS2.pdf", course) == "exercise"
    assert detect_type(tmp_path / "Homework1 - Part1.pdf", course) == "exercise"
    assert detect_lecture("03_invarianceStability_handout") == 3
    assert default_key(tmp_path / "03_invarianceStability_handout.pdf", "slides", 3) == "L03-slides"


def test_colliding_auto_keys_fall_back_to_path(project, tmp_path):
    paths, course, con = project
    d = tmp_path / "repo"
    for sub in ("ex00", "ex01"):
        (d / sub).mkdir(parents=True)
        (d / sub / "Readme.md").write_text(f"# {sub}\nreadme for {sub}\n")
    (d / "build").mkdir()
    (d / "build" / "junk.cpp").write_text("int x;")
    (d / "ex00" / "a.out").write_bytes(b"\x7fELF")
    first = ingest(con, paths, course, str(d), copy=False)
    assert sorted(r.key for r in first) == ["ex01-readme", "note-readme"]
    assert {r.status for r in ingest(con, paths, course, str(d), copy=False)} == {"unchanged"}


def test_relabel_keeps_chunks(project, materials):
    from study.ingest.pipeline import relabel_source
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    n = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    relabel_source(con, "demo", "L03-rec", new_key="L05-rec", lecture=5)
    row = con.execute("SELECT lecture FROM sources WHERE key='L05-rec'").fetchone()
    assert row["lecture"] == 5 and con.execute("SELECT count(*) FROM chunks").fetchone()[0] == n
    # re-ingesting the same file keeps the user's label instead of creating a second copy
    [r] = ingest(con, paths, course, str(materials / "L03.srt"), copy=False)
    assert (r.status, r.key) == ("unchanged", "L05-rec")
