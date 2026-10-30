import shutil

import pytest

from study.ingest.pipeline import ingest
from study.practice.questions import (QuestionError, add, get, listing, log_attempt, new_session, pick, rubric,
                                      run_tests)

needs_gxx = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


def _write(d, files):
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return d


def rubric_q(tmp_path, name="q1", points=4, cite="[@demo:L03-slides#p2]", title="Why std::move is a cast"):
    return _write(tmp_path / name, {
        "q.md": f"---\ntitle: {title}\ntopic: Move semantics\nlecture: 3\nkind: explain\ndifficulty: easy\n"
                f"points: {points}\ncites: ['{cite}']\n---\nExplain what `std::move` actually does.\n",
        "solution.md": "It is a cast to an rvalue reference.\n\n## Rubric\n- (2) says it is a cast\n- (2) says nothing moves until a move ctor runs\n",
    })


CODE_FILES = {
    "q.md": "---\ntitle: Off-by-one in sum\ntopic: Loops\nlecture: 3\nkind: find-bug\ndifficulty: easy\npoints: 3\n"
            "cites: ['[@demo:ex02/vector.cpp#L5-10]']\ngrading: {mode: unit}\n---\nFind and fix the bug in `sum`.\n",
    "solution.md": "The loop skips the last element.\n\n## Rubric\n- (2) finds the off-by-one\n- (1) fix passes the tests\n",
    "src/solution.hpp": "#pragma once\n#include <vector>\ninline int sum(const std::vector<int>& v) {\n"
                        "  int s = 0;\n  for (std::size_t i = 0; i + 1 < v.size(); ++i) s += v[i];\n  return s;\n}\n",
    "ref/solution.hpp": "#pragma once\n#include <vector>\ninline int sum(const std::vector<int>& v) {\n"
                        "  int s = 0;\n  for (std::size_t i = 0; i < v.size(); ++i) s += v[i];\n  return s;\n}\n",
    "tests/test.cpp": '#include "solution.hpp"\n#include <cassert>\nint main() { assert(sum({1, 2, 3}) == 6); '
                      'assert(sum({}) == 0); return 0; }\n',
}


@pytest.fixture
def bank(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    return paths, course, con


def test_rubric_question_roundtrip(bank, tmp_path):
    paths, course, con = bank
    qid, warnings = add(con, course, rubric_q(tmp_path), paths.data / "build")
    assert warnings == [] and (course.dir / "questions" / qid / "q.md").exists()
    q = get(con, qid)
    assert q.meta["kind"] == "explain" and rubric(q.solution) == [(2.0, "says it is a cast"),
                                                                  (2.0, "says nothing moves until a move ctor runs")]


@pytest.mark.parametrize("kwargs,msg", [({"points": 5}, "rubric points sum to 4"),
                                        ({"cite": "[@demo:L03-slides#p99]"}, "out of range")])
def test_rejects_inconsistent_questions(bank, tmp_path, kwargs, msg):
    paths, course, con = bank
    with pytest.raises(QuestionError, match=msg):
        add(con, course, rubric_q(tmp_path, **kwargs), paths.data / "build")


@needs_gxx
def test_code_question_validated_by_compiling(bank, tmp_path):
    paths, course, con = bank
    qid, _ = add(con, course, _write(tmp_path / "code", CODE_FILES), paths.data / "build")
    q = get(con, qid)
    assert run_tests(q, q.dir / "ref", paths.data / "build")["passed"]
    starter = run_tests(q, q.dir / "src", paths.data / "build")
    assert not starter["passed"] and "tests fail" in starter["summary"]

    # a "find-bug" whose starter already passes is rejected
    broken = dict(CODE_FILES, **{"src/solution.hpp": CODE_FILES["ref/solution.hpp"]})
    with pytest.raises(QuestionError, match="already passes"):
        add(con, course, _write(tmp_path / "code2", broken), paths.data / "build")


@needs_gxx
def test_session_answer_tests_and_attempts(bank, tmp_path):
    paths, course, con = bank
    q_rub, _ = add(con, course, rubric_q(tmp_path), paths.data / "build")
    q_code, _ = add(con, course, _write(tmp_path / "code", CODE_FILES), paths.data / "build")
    sdir = new_session(con, course, [q_rub, q_code])
    text = (sdir / "answer.md").read_text()
    assert f"<!-- id: {q_rub} -->" in text and "### Your answer" in text
    # student fixes the bug in the session copy
    (sdir / q_code / "src" / "solution.hpp").write_text(CODE_FILES["ref/solution.hpp"])
    assert run_tests(get(con, q_code), sdir / q_code / "src", paths.data / "build")["passed"]

    log_attempt(con, q_code, sdir.name, 1.0, points=3)
    log_attempt(con, q_rub, sdir.name, 0.5, points=2)
    with pytest.raises(QuestionError):
        log_attempt(con, q_rub, sdir.name, 1.5)
    with pytest.raises(QuestionError):
        log_attempt(con, q_rub, sdir.name, 0.5, points=9)
    rows = {r["id"]: r for r in listing(con, "demo")}
    assert rows[q_code]["last_score"] == 1.0 and rows[q_rub]["n_attempts"] == 1


def test_pick_prefers_new_then_weak(bank, tmp_path):
    paths, course, con = bank
    ids = [add(con, course, rubric_q(tmp_path, f"q{i}", title=f"Question {i}"), paths.data / "build")[0]
           for i in range(3)]
    log_attempt(con, ids[0], "s", 0.9)
    log_attempt(con, ids[1], "s", 0.2)
    order = [r["id"] for r in pick(con, "demo", n=3)]
    assert order == [ids[2], ids[1], ids[0]]


def test_answers_roundtrip(bank, tmp_path):
    from study.practice.questions import read_answers, write_answers
    paths, course, con = bank
    a, _ = add(con, course, rubric_q(tmp_path, "qa", title="Alpha"), paths.data / "build")
    b, _ = add(con, course, rubric_q(tmp_path, "qb", title="Beta"), paths.data / "build")
    sdir = new_session(con, course, [a, b])
    write_answers(sdir, {a: "It is a cast to T&&.", b: "Line 1\n\nLine 2 with $x^2$"})
    assert read_answers(sdir) == {a: "It is a cast to T&&.", b: "Line 1\n\nLine 2 with $x^2$"}
    write_answers(sdir, {a: "changed", b: "Line 1\n\nLine 2 with $x^2$"})
    assert read_answers(sdir)[a] == "changed" and "Explain what `std::move`" in (sdir / "answer.md").read_text()
