import pytest

from study.digest import DigestError, save, todo, validate
from study.ingest.pipeline import ingest

GOOD = """# W3: Move semantics

## Overview
Moving instead of copying [@demo:L03-slides#p1].

## Concepts
### Rvalue references
**Intuition**: temporaries can be robbed [@demo:L03-rec#t=00:00:45].
**Example**: `std::string s = f();`
**Formal**: `T&&` binds to rvalues [@demo:L03-slides#p2].
**Pitfalls**: a named rvalue reference is an lvalue.
**Connections**: W2 (value categories) (general knowledge).

### std::move
**Intuition**: it's just a cast [@demo:L03-rec#t=00:01:35].
**Example**: `v2 = std::move(v1);`

## Glossary
- **rvalue**: an expression whose resources may be reused [@demo:L03-slides#p2]
- **std::move**: a cast to an rvalue reference [@demo:L03-rec#t=00:01:35]

## Key results
- **Rule of five**: if you define one special member, define all five (general knowledge) [@demo:L03-slides#p3]

## What an exam would ask
1. Explain why `std::move` doesn't move anything. *(explain; easy)*
2. Predict the output of a program using moved-from strings. *(predict-output; medium)*
3. Implement a move constructor. *(implement; medium)*

## Gaps & uncertainties
None.
"""


@pytest.fixture
def lecture3(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    return paths, course, con


def test_todo_tracks_missing_fresh_stale(lecture3, materials):
    paths, course, con = lecture3
    [item] = todo(con, "demo")
    assert (item.lecture, item.status) == (3, "missing")
    assert set(item.sources) == {"L03-slides", "L03-rec"}  # code and notes don't define a digest

    path, v = save(con, course, 3, GOOD)
    assert path.exists() and v.cites >= 5 and len(v.glossary) == 2 and v.exam_questions == 3
    assert "std::move" in v.warnings[0] and "Pitfalls" in v.warnings[0]  # incomplete concept is a warning
    assert todo(con, "demo")[0].status == "fresh"

    # unrelated change (my notes) keeps it fresh; a change to the lecture's slides makes it stale
    (materials / "ex02" / "notes.md").write_text("# changed\n")
    ingest(con, paths, course, str(materials), copy=False)
    assert todo(con, "demo")[0].status == "fresh"
    (materials / "L03.srt").write_text((materials / "L03.srt").read_text() + "\n4\n00:02:10,000 --> 00:02:20,000\nbye\n")
    ingest(con, paths, course, str(materials), copy=False)
    assert todo(con, "demo")[0].status == "stale"


def test_rejects_bad_digests(lecture3):
    _, _, con = lecture3
    with pytest.raises(DigestError, match="out of range"):
        validate(con, "demo", GOOD.replace("#p3]", "#p30]"))
    with pytest.raises(DigestError, match="missing section '## Glossary'"):
        validate(con, "demo", GOOD.replace("## Glossary", "## Terms"))
    with pytest.raises(DigestError, match="numbered questions"):
        validate(con, "demo", GOOD.split("## What an exam")[0] + "## What an exam would ask\n- one\n")
    with pytest.raises(DigestError, match="citations"):
        validate(con, "demo", "# T\n## Overview\n## Concepts\n### A\n## Glossary\n- **a**: b\n"
                              "## Key results\n- **a**: b\n## What an exam would ask\n1. a\n2. b\n3. c\n")


def test_save_writes_frontmatter_and_course_indexes(lecture3):
    _, course, con = lecture3
    path, _ = save(con, course, 3, GOOD)
    text = path.read_text()
    assert text.startswith("---\ncourse: demo\nlecture: 3\n") and "input_hash:" in text
    gloss = (course.dir / "glossary.md").read_text()
    assert "**rvalue**" in gloss and "[W3](digests/L03.md)" in gloss
    assert "Rule of five" in (course.dir / "key_results.md").read_text()
    save(con, course, 3, GOOD)  # re-saving replaces, doesn't duplicate
    assert con.execute("SELECT count(*) FROM glossary").fetchone()[0] == 2
