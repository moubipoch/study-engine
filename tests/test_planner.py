import datetime as dt

from study import config
from study.ingest.pipeline import ingest
from study.plan.planner import Settings, build_plan, to_ics


def _settings(**kw):
    s = Settings({d: 3.0 for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _course(project, exam="2027-01-20", prio=4, slug="demo"):
    paths, course, con = project
    (course.dir / "course.yaml").write_text(f"name: Demo\nexam_date: {exam}\npriority: {prio}\n")
    return config.load_course(course.dir)


def test_capacity_blocked_dates_and_no_overlap(project, materials):
    paths, _, con = project
    ingest(con, paths, _course(project), str(materials), copy=False)
    c = _course(project)
    start = dt.date(2026, 11, 2)
    plan = build_plan(con, {"demo": c}, _settings(blocked_dates=[start + dt.timedelta(days=1)]), start, days=3)
    by_day = {}
    for b in plan["blocks"]:
        by_day.setdefault(b["date"], []).append(b)
    assert (start + dt.timedelta(days=1)).isoformat() not in by_day  # blocked
    for day, bs in by_day.items():
        mins = sum((dt.datetime.strptime(b["end"], "%H:%M") - dt.datetime.strptime(b["start"], "%H:%M")).seconds / 60 for b in bs)
        assert mins <= 180
        ends = [b["end"] for b in bs]
        assert all(bs[i + 1]["start"] >= ends[i] for i in range(len(bs) - 1))  # no overlaps
    assert any("Learn week 3" in b["title"] and b["command"] == "/teach demo 3" for b in plan["blocks"])


def test_never_after_exam_and_missing_date_is_not_guessed(project, materials):
    paths, _, con = project
    ingest(con, paths, _course(project), str(materials), copy=False)
    c = _course(project, exam="2026-11-03")
    plan = build_plan(con, {"demo": c}, _settings(), dt.date(2026, 11, 1), days=5)
    assert plan["blocks"] and all(b["date"] <= "2026-11-03" for b in plan["blocks"] if b["course"] == "demo")
    nodate = _course(project, exam="")
    plan = build_plan(con, {"demo": nodate}, _settings(), dt.date(2026, 11, 1), days=2)
    assert any("no exam date" in w for w in plan["warnings"]) and plan["blocks"]  # steady mode, still planned


def test_winter_break_capacity_and_ics(project, materials):
    paths, _, con = project
    ingest(con, paths, _course(project), str(materials), copy=False)
    c = _course(project, exam="2027-01-25")
    s = _settings(winter_start=dt.date(2026, 12, 21), winter_end=dt.date(2027, 1, 3), winter_hours=6)
    assert s.minutes(dt.date(2026, 12, 22)) == 360 and s.minutes(dt.date(2026, 12, 18)) == 180
    plan = build_plan(con, {"demo": c}, s, dt.date(2026, 12, 21), days=2)
    assert plan["winter_days"] == ["2026-12-21", "2026-12-22"]
    ics = to_ics(plan)
    assert ics.count("BEGIN:VEVENT") == len(plan["blocks"]) and "DTSTART;TZID=Europe/Zurich:20261221T0900" in ics
    assert len({b["uid"] for b in plan["blocks"]}) == len(plan["blocks"])


def test_catchup_only_for_lectures_already_given(project, materials):
    paths, _, con = project
    ingest(con, paths, _course(project), str(materials), copy=False)  # material is week 3
    c = _course(project)
    early = _settings(semester_start=dt.date(2026, 11, 2))  # day 1 of the semester: week 3 not given yet
    plan = build_plan(con, {"demo": c}, early, dt.date(2026, 11, 2), days=1)
    assert not any("Catch up week 3" in b["title"] for b in plan["blocks"])
