import datetime as dt

import pytest

from study import config


def _course(tmp_path, slug="demo", body=None):
    d = tmp_path / "courses" / slug
    d.mkdir(parents=True)
    (d / "course.yaml").write_text(body or f"""slug: {slug}
name: Demo
exam_date: 2027-01-20
priority: 4  # comment kept
style: {{kind: programming}}

{config.MANAGED_MARKER}
topics: []
topics_proposed: [a, b]
""")
    (tmp_path / "pyproject.toml").write_text("")
    return d


def test_load_and_validate(tmp_path):
    _course(tmp_path)
    cs = config.load_courses(config.Paths(tmp_path))
    c = cs["demo"]
    assert c.exam_date == dt.date(2027, 1, 20) and c.priority == 4 and c.topics_proposed == ["a", "b"]
    assert c.anki_deck == "ETH::DEMO"


def test_project_only_and_missing_date(tmp_path):
    _course(tmp_path, "p", "name: P\nexam_date: project-only\n")
    _course(tmp_path / "x", "q", "name: Q\nexam_date:\n")
    assert config.load_course(tmp_path / "courses" / "p").project_only
    q = config.load_course(tmp_path / "x" / "courses" / "q")
    assert q.exam_date is None and not q.project_only


@pytest.mark.parametrize("body", ["name: X\npriority: 9\n", "exam_date: 2027-01-01\n",
                                  "name: X\nexam_date: next week\n", "slug: other\nname: X\n"])
def test_invalid(tmp_path, body):
    d = _course(tmp_path, "bad", body)
    with pytest.raises(config.ConfigError):
        config.load_course(d)


def test_inactive_is_config_only(tmp_path):
    _course(tmp_path, "gone", "name: G\nactive: false\n")
    p = config.Paths(tmp_path)
    assert "gone" not in config.load_courses(p)
    assert "gone" in config.load_courses(p, include_inactive=True)


def test_update_preserves_handwritten_part(tmp_path):
    d = _course(tmp_path)
    c = config.load_course(d)
    config.update_course_yaml(c, topics=["Templates", "STL"], topics_proposed=[])
    text = (d / "course.yaml").read_text()
    assert "# comment kept" in text
    c2 = config.load_course(d)
    assert c2.topics == ["Templates", "STL"] and c2.topics_proposed == []
    with pytest.raises(config.ConfigError):
        config.update_course_yaml(c2, priority=1)
