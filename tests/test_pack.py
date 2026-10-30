import json

from study.ingest.pipeline import ingest
from study.pack import build


def test_pack_builds_course_folder_with_vault_commands(project, materials, tmp_path):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False, lecture=3)
    out = tmp_path / "pack"
    stats = build(con, paths, course, out)
    assert stats["transcripts"] == 1 and "[00:00:00]" in (out / "W03" / "Lecture recording L03.md").read_text()
    for f in ("CLAUDE.md", "INDEX.md", ".claude/commands/wrap.md", ".claude/commands/check.md",
              ".claude/commands/exam-model.md", ".claude/commands/readiness.md", ".claude/commands/gaps.md"):
        assert (out / f).exists(), f
    ins = (out / "CLAUDE.md").read_text()
    assert "StudyVault" in ins and "«" not in ins and "study readiness demo" in ins
    s = json.loads((out / ".claude" / "settings.json").read_text())
    assert any(a.startswith("Edit(/") and "StudyVault" in a for a in s["permissions"]["allow"])
    # your own instructions and settings survive a rebuild
    (out / "CLAUDE.md").write_text("my own instructions")
    (out / ".claude" / "settings.json").write_text('{"permissions": {"allow": ["Read"]}}')
    build(con, paths, course, out)
    assert (out / "CLAUDE.md").read_text() == "my own instructions"
    assert json.loads((out / ".claude" / "settings.json").read_text()) == {"permissions": {"allow": ["Read"]}}
    assert not (out / "progress").exists()
