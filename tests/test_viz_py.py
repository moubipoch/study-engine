import pytest

from study.ingest.pipeline import ingest
from study.practice.questions import add, get, run_tests
from study.viz import ExprError, bifurcation_1d, compile_expr, phase_portrait
from tests.test_questions import _write


def test_fixed_point_classification():
    kinds = {(round(p["x"], 2), round(p["y"], 2)): p["kind"] for p in phase_portrait("y", "-sin(x)", (-4, 4), (-2, 2))["fixed_points"]}
    assert "saddle" in kinds[(3.14, 0.0)] and "center" in kinds[(0.0, 0.0)]  # undamped pendulum
    [p] = phase_portrait("-x + y", "-x - y", (-1, 1), (-1, 1))["fixed_points"]
    assert p["kind"].startswith("stable spiral")
    [p] = phase_portrait("x", "2*y", (-1, 1), (-1, 1))["fixed_points"]
    assert p["kind"] == "unstable node"


def test_pitchfork_and_saddle_node_locations(tmp_path):
    res = bifurcation_1d("r*x - x**3", "r", (-1, 1), (-2, 2), tmp_path / "b.png")
    assert any(abs(b) < 0.02 for b in res["bifurcation_points"]) and (tmp_path / "b.png").exists()
    res = bifurcation_1d("r + x**2", "r", (-1, 1), (-2, 2))
    assert any(abs(b) < 0.02 for b in res["bifurcation_points"])


@pytest.mark.parametrize("bad", ["__import__('os').system('true')", "x.__class__", "open('f')", "lambda: 1", "[1,2]"])
def test_expressions_are_whitelisted(bad):
    with pytest.raises((ExprError, SyntaxError)):
        compile_expr(bad, ("x", "y"))


PY_Q = {
    "q.md": "---\ntitle: GP posterior mean\ntopic: Gaussian processes\nlecture: 3\nkind: implement\ndifficulty: medium\n"
            "points: 2\ncites: ['[@demo:L03-slides#p1]']\ngrading: {mode: py}\n---\nImplement `posterior_mean`.\n",
    "solution.md": "Solve K a = y.\n\n## Rubric\n- (1) uses a linear solve\n- (1) tests pass\n",
    "src/solution.py": "def posterior_mean(K, k_star, y):\n    raise NotImplementedError\n",
    "ref/solution.py": "import numpy as np\n\ndef posterior_mean(K, k_star, y):\n    return k_star @ np.linalg.solve(K, y)\n",
    "tests/test_solution.py": "import numpy as np\nfrom solution import posterior_mean\n\n"
                              "def test_mean():\n    K = np.array([[2., .5], [.5, 1.]]); y = np.array([1., 2.])\n"
                              "    assert np.allclose(posterior_mean(K, np.array([1., 0.]), y), np.array([1., 0.]) @ np.linalg.solve(K, y))\n",
}


def test_python_question_graded_with_pytest(project, materials, tmp_path):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    qid, _ = add(con, course, _write(tmp_path / "pyq", PY_Q), paths.data / "build")
    q = get(con, qid)
    assert run_tests(q, q.dir / "ref", paths.data / "build")["passed"]
    assert not run_tests(q, q.dir / "src", paths.data / "build")["passed"]
