import pytest

from study.brief import BriefError, follow_ups, save, status
from study.ingest.pipeline import ingest

GOOD = """# W3: Move semantics

**In one sentence:** moves steal resources instead of copying them.

## The big picture
Copies are expensive; moves transfer ownership.

## Key ideas
- **Rvalue references**: bind to temporaries [@demo:L03-slides#p1]
- **std::move**: just a cast [@demo:L03-rec#t=00:01:35]
- **Ownership**: one owner [@demo:L03-slides#p3]

## Go deeper
1. Why is `std::move` only a cast? [@demo:L03-slides#p2]
2. When does a move actually happen?
"""


def test_brief_save_status_and_follow_ups(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False, lecture=3)
    assert status(con, "demo") == {3: "missing"}
    with pytest.raises(BriefError, match="Go deeper"):
        save(con, course, 3, GOOD.split("## Go deeper")[0])
    with pytest.raises(BriefError, match="out of range"):
        save(con, course, 3, GOOD.replace("#p3]", "#p99]"))
    path, info = save(con, course, 3, GOOD)
    assert info["cites"] == 4 and status(con, "demo") == {3: "fresh"}
    assert follow_ups(path.read_text()) == ["Why is `std::move` only a cast?", "When does a move actually happen?"]
