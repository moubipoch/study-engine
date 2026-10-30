import pytest

from study import config
from study.ingest.pipeline import ingest
from study.topics import TopicError, approve, propose, remove


def test_propose_approve_remove(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    (course.dir / "course.yaml").write_text((course.dir / "course.yaml").read_text() + "\n" + config.MANAGED_MARKER + "\n")
    course = config.load_course(course.dir)
    propose(con, course, [
        {"name": "Move semantics", "summary": "rvalues", "lectures": [3], "cites": ["[@demo:L03-slides#p1]"]},
        "Templates",
        {"name": "move semantics"},  # duplicate (case-insensitive) is dropped
    ])
    course = config.load_course(course.dir)
    assert [t["name"] for t in course.topics_proposed] == ["Move semantics", "Templates"]

    took, left = approve(course, ["templates"])
    assert took == ["Templates"] and left == ["Move semantics"]
    course = config.load_course(course.dir)
    approve(course)
    course = config.load_course(course.dir)
    assert course.topic_names == ["Templates", "Move semantics"] and course.topics_proposed == []
    assert course.priority == 3  # hand-written part untouched

    remove(course, ["Templates"])
    assert config.load_course(course.dir).topic_names == ["Move semantics"]


def test_propose_rejects_bad_evidence(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    with pytest.raises(TopicError, match="out of range"):
        propose(con, course, [{"name": "X", "cites": ["[@demo:L03-slides#p99]"]}])
    with pytest.raises(TopicError):
        approve(config.load_course(course.dir), ["nonexistent"])
