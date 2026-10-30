import pytest

from study.ingest.pipeline import ingest
from study.srs.mastery import mastery
from study.teach import TeachError, get, listing, log_check, norm_target, progress, save_plan

PLAN = [
    {"name": "Rvalue references", "topic": "Move semantics", "cites": ["[@demo:L03-slides#p1]"]},
    {"name": "std::move is a cast", "topic": "Move semantics", "cites": ["[@demo:L03-rec#t=00:01:35]"]},
    {"name": "Ownership", "cites": "[@demo:L03-slides#p3]"},
]


@pytest.fixture
def lesson(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    return con


def test_targets_normalise():
    assert norm_target("3") == norm_target("W3") == norm_target("w03") == norm_target("L3") == "W03"
    assert norm_target("Terminal set!") == "topic-terminal-set"
    assert norm_target("topic-x") == "topic-x"


def test_plan_needs_valid_citations(lesson):
    with pytest.raises(TeachError, match="out of range"):
        save_plan(lesson, "demo", "3", "W3", [{"name": "X", "cites": ["[@demo:L03-slides#p99]"]}])
    with pytest.raises(TeachError, match="needs at least one citation"):
        save_plan(lesson, "demo", "3", "W3", [{"name": "X", "cites": []}])
    s = save_plan(lesson, "demo", "W3", "W3: moves", PLAN)
    assert s["total"] == 3 and s["position"] == 0 and s["status"] == "active"


def test_progress_checks_and_resume(lesson):
    save_plan(lesson, "demo", "3", "W3: moves", PLAN)
    log_check(lesson, "demo", "3", 1)                        # concept 0, topic from the plan
    progress(lesson, "demo", "3", advance=True)
    log_check(lesson, "demo", "3", 0)
    s = progress(lesson, "demo", "3", struggle="thinks std::move moves")
    assert s["position"] == 1 and s["struggles"][0]["concept"] == "std::move is a cast"
    assert [c["score"] for c in get(lesson, "demo", "W03")["checks"]] == [1, 0]
    # re-planning keeps progress when the taught part is unchanged, resets it otherwise
    assert save_plan(lesson, "demo", "3", "W3", PLAN + [{"name": "RAII", "cites": ["[@demo:L03-slides#p2]"]}])["position"] == 1
    assert save_plan(lesson, "demo", "3", "W3", list(reversed(PLAN)))["position"] == 0
    progress(lesson, "demo", "3", position=99)
    assert get(lesson, "demo", "3")["status"] == "done"
    row = listing(lesson, "demo")[0]
    assert row["checks"] == 2 and row["check_score"] == 0.5 and row["next"] is None


def test_checks_feed_mastery(lesson, project):
    _, course, _ = project
    save_plan(lesson, "demo", "3", "W3", PLAN)
    log_check(lesson, "demo", "3", 1)
    log_check(lesson, "demo", "3", 0.5)
    m = {r["topic"]: r for r in mastery(lesson, course)}
    assert m["Move semantics"]["mastery"] == pytest.approx(0.75, abs=0.01)
    with pytest.raises(TeachError):
        log_check(lesson, "demo", "3", 2)
