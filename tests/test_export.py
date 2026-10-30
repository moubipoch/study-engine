from study import config
from study.digest import save
from study.export.obsidian import concept_key, export
from study.ingest.pipeline import ingest
from tests.test_digest import GOOD


def test_vault_links_concepts_across_courses_and_keeps_user_edits(project, materials, tmp_path):
    paths, course, con = project
    d2 = paths.courses / "demo2"
    d2.mkdir()
    (d2 / "course.yaml").write_text("name: Second Course\nexam_date: 2027-02-01\n")
    c2 = config.load_course(d2)
    ingest(con, paths, course, str(materials), copy=False)
    ingest(con, paths, c2, str(materials), copy=False, force=True)
    save(con, course, 3, GOOD)
    save(con, c2, 3, GOOD.replace("@demo:", "@demo2:"))
    vault = tmp_path / "vault"
    res = export(con, {"demo": course, "demo2": c2}, vault)
    assert res["written"] > 0
    concept = (vault / "Concepts" / "rvalue.md").read_text()
    assert "Appears in **2 courses**" in concept and "[[Demo/Week 03|week 3]]" in concept
    week = (vault / "Demo" / "Week 03.md").read_text()
    assert "[[rvalue]]" in week and "http://127.0.0.1:8090/cite?c=" in week and "[@demo:" not in week

    # the user edits a generated note and adds their own note; re-export keeps both
    (vault / "Demo" / "Week 03.md").write_text(week + "\nmy own insight\n")
    (vault / "Demo" / "My notes.md").write_text("# mine\n")
    res = export(con, {"demo": course, "demo2": c2}, vault)
    assert "my own insight" in (vault / "Demo" / "Week 03.md").read_text()
    assert (vault / "Demo" / "My notes.md").read_text() == "# mine\n"
    assert res["kept_your_edits"] >= 1


def test_concept_key_normalises():
    assert concept_key("`std::move` (cast)") == concept_key("std::move") == "std::move"
