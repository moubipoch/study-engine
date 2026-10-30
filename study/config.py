"""Course configuration: everything course-specific lives in courses/<slug>/course.yaml."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ONLY = "project-only"


class ConfigError(ValueError):
    pass


def find_root(start: Path | None = None) -> Path:
    """Workspace root = $STUDY_ROOT, or the nearest ancestor containing courses/ and pyproject.toml (`study init`)."""
    if env := os.environ.get("STUDY_ROOT"):
        return Path(env).resolve()
    p = (start or Path.cwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / "courses").is_dir() and (cand / "pyproject.toml").is_file():
            return cand
    raise ConfigError("Not inside a study workspace: run `study init <folder>` once, then work inside it "
                      "(or set STUDY_ROOT).")


@dataclass
class Paths:
    root: Path

    @property
    def courses(self) -> Path:
        return self.root / "courses"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db(self) -> Path:
        return self.data / "study.db"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    def course_dir(self, slug: str) -> Path:
        return self.courses / slug

    @property
    def settings(self) -> dict:
        """Workspace settings (study.yaml next to courses/): vault, where course folders live, planner, profile."""
        return _settings(self.root)

    def _path(self, key: str, default: str) -> Path:
        p = Path(os.path.expanduser(str(self.settings.get(key) or default)))
        return (p if p.is_absolute() else self.root / p).resolve()

    @property
    def vault(self) -> Path:
        """The knowledge vault (Obsidian + git)."""
        return self._path("vault", "../StudyVault")

    @property
    def folders_root(self) -> Path:
        """Where each course's own folder lives (<folders_root>/<course folder>/Claude …)."""
        return self._path("folders_root", "..")


@lru_cache(maxsize=8)
def _settings(root: Path) -> dict:
    f = root / "study.yaml"
    return (yaml.safe_load(f.read_text()) or {}) if f.exists() else {}


@dataclass
class Course:
    slug: str
    name: str
    code: str
    active: bool
    exam_date: dt.date | None  # None = not set yet (planner refuses to guess)
    project_only: bool
    exam_format: str
    priority: int
    topics: list[str]
    topics_proposed: list[str]
    style: dict
    anki_deck: str
    dir: Path
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def has_exam(self) -> bool:
        return not self.project_only

    @property
    def short(self) -> str:
        """Short display name (vault folders, labels): course.yaml `short`, else the slug in capitals."""
        return str(self.raw.get("short") or self.slug.upper())

    @property
    def folder(self) -> str:
        """Name of this course's own folder under the workspace's folders_root (course.yaml `folder`)."""
        return str(self.raw.get("folder") or self.short)

    @property
    def planner_name(self) -> str:
        """How the course is called in the planner connector (course.yaml `planner_name`)."""
        return str(self.raw.get("planner_name") or self.short)

    @property
    def topic_names(self) -> list[str]:
        return [topic_name(t) for t in self.topics]


def topic_name(t) -> str:
    """Topics are plain strings or dicts {name, summary, lectures, cites}."""
    return str(t.get("name", "")) if isinstance(t, dict) else str(t)


def _parse_exam_date(slug: str, value) -> tuple[dt.date | None, bool]:
    if value in (None, ""):
        return None, False
    if value == PROJECT_ONLY:
        return None, True
    if isinstance(value, dt.date):
        return value, False
    try:
        return dt.date.fromisoformat(str(value)), False
    except ValueError:
        raise ConfigError(f"{slug}: exam_date must be YYYY-MM-DD, '{PROJECT_ONLY}', or empty; got {value!r}")


def load_course(course_dir: Path) -> Course:
    path = course_dir / "course.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    slug = course_dir.name
    if raw.get("slug", slug) != slug:
        raise ConfigError(f"{path}: slug {raw.get('slug')!r} does not match directory name {slug!r}")
    if not raw.get("name"):
        raise ConfigError(f"{path}: 'name' is required")
    prio = raw.get("priority", 3)
    if not isinstance(prio, int) or not 1 <= prio <= 5:
        raise ConfigError(f"{path}: priority must be an int 1..5 (5 = highest), got {prio!r}")
    exam_date, project_only = _parse_exam_date(slug, raw.get("exam_date"))
    return Course(
        slug=slug,
        name=raw["name"],
        code=str(raw.get("code") or ""),
        active=bool(raw.get("active", True)),
        exam_date=exam_date,
        project_only=project_only,
        exam_format=str(raw.get("exam_format") or ""),
        priority=prio,
        topics=list(raw.get("topics") or []),
        topics_proposed=list(raw.get("topics_proposed") or []),
        style=dict(raw.get("style") or {}),
        anki_deck=raw.get("anki_deck") or f"ETH::{slug.upper()}",
        dir=course_dir,
        raw=raw,
    )


def load_courses(paths: Paths, include_inactive: bool = False) -> dict[str, Course]:
    out = {}
    for d in sorted(paths.courses.iterdir()):
        if (d / "course.yaml").is_file():
            c = load_course(d)
            if c.active or include_inactive:
                out[c.slug] = c
    return out


def get_course(paths: Paths, slug: str) -> Course:
    d = paths.course_dir(slug)
    if not (d / "course.yaml").is_file():
        known = ", ".join(load_courses(paths, include_inactive=True)) or "none"
        raise ConfigError(f"Unknown course {slug!r}. Known: {known}")
    return load_course(d)


MANAGED_MARKER = "# ---- managed by `study` below this line (topics); edit freely, keep the marker ----"


def update_course_yaml(course: Course, **changes) -> None:
    """Rewrite managed keys (e.g. topics) below MANAGED_MARKER; the hand-written part above is untouched."""
    path = course.dir / "course.yaml"
    text = path.read_text()
    head, _, tail = text.partition(MANAGED_MARKER)
    managed = yaml.safe_load(tail) or {} if tail else {}
    head_keys = set((yaml.safe_load(head) or {}).keys())
    if clash := head_keys & changes.keys():
        raise ConfigError(f"{path}: keys {sorted(clash)} must live below the managed marker")
    managed.update(changes)
    body = yaml.safe_dump(managed, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(head.rstrip() + "\n\n" + MANAGED_MARKER + "\n" + body)


@lru_cache(maxsize=1)
def default_paths() -> Paths:
    return Paths(find_root())
