import datetime as dt

from study.vault import readiness, swiss_grade, write_readiness

TODAY = dt.date(2026, 11, 1)


def _vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Courses" / "PAI").mkdir(parents=True)
    (v / "Courses" / "PAI" / "Exam model.md").write_text("""---
course: pai
confidence: high
topics:
  - {name: Gaussian processes, weight: 30, concepts: 2, styles: [true/false, compute]}
  - {name: Variational inference, weight: 20}
  - {name: Reinforcement learning, weight: 50}
---
""")
    (v / "Concepts").mkdir()
    def node(name, **fm):
        body = "\n".join(f"{k}: {val}" for k, val in fm.items())
        (v / "Concepts" / f"{name}.md").write_text(f"---\n{body}\n---\n")
    node("GP posterior", course="[pai]", topic="Gaussian processes", mastery=1.0, evidence=3,
         last_tested="2026-11-01", next_review="2026-11-05", streak=2)
    node("Kernels", course="[pai]", topic="gaussian processes", mastery=0.5, evidence=1,
         last_tested="2026-10-25", next_review="2026-10-30", streak=0)
    node("ELBO", course="[pai]", topic="Variational inference", mastery=0.0, evidence=0)
    node("Bellman", course="[dpoc, pai]", topic="Something else", mastery=0.8, evidence=1, last_tested="2026-11-01")
    return v


def test_readiness_weights_mastery_forgetting_and_ranking(tmp_path):
    v = _vault(tmp_path)
    r = readiness(v, "pai", dt.date(2027, 1, 20), TODAY)
    t = {s.name: s for s in r.topics}
    assert abs(t["Gaussian processes"].weight - 0.3) < 1e-9
    gp = t["Gaussian processes"]
    assert gp.concepts == 2 and gp.tested == 2 and gp.due == 1          # Kernels is overdue
    assert 0.5 < gp.mastery < 0.75                                       # 1.0 fresh + 0.5 decayed after 7 days, over 2
    assert t["Variational inference"].mastery == 0 and t["Variational inference"].tested == 0
    assert t["Reinforcement learning"].concepts == 0
    assert r.next_actions(1)[0].name == "Reinforcement learning"         # biggest share, nothing known
    assert abs(r.expected_share - 0.3 * gp.mastery) < 1e-9
    assert r.tested_share == 0.3 and r.unmapped == ["Bellman"]


def test_write_readiness_keeps_one_history_line_per_day(tmp_path):
    v = _vault(tmp_path)
    r = readiness(v, "pai", None, TODAY)
    p = write_readiness(v, r)
    write_readiness(v, r)
    text = p.read_text()
    assert text.count("| 2026-11-01 |") == 1 and "Next best actions" in text
    (p.parent / "Readiness.md").write_text(text.replace("## History", "my own note\n\n## History"))
    write_readiness(v, readiness(v, "pai", None, TODAY + dt.timedelta(days=1)))
    text = p.read_text()
    assert "my own note" in text and "| 2026-11-02 |" in text


def test_grade_scale():
    assert swiss_grade(1.0) == 6 and swiss_grade(0.6) == 4 and swiss_grade(0.0) == 1


def test_next_actions_only_suggest_lectured_topics(tmp_path):
    v = _vault(tmp_path)
    p = v / "Courses" / "PAI" / "Exam model.md"
    p.write_text(p.read_text().replace("{name: Reinforcement learning, weight: 50}",
                                       "{name: Reinforcement learning, weight: 50, weeks: [10, 11]}"))
    r = readiness(v, "pai", None, TODAY, semester_start=dt.date(2026, 9, 14))   # week 7
    assert r.week == 7 and "Reinforcement learning" not in [t.name for t in r.next_actions()]
    assert [t.name for t in r.later()] == ["Reinforcement learning"]
