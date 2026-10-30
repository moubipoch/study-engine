"""`study sync`: fetch everything new with coursesync, then ingest it here.

coursesync (../coursesync, its own repo) does all the downloading: course repos, websites, video.ethz.ch,
Moodle, Zoom; it keeps the saved ETH login and opens a login window only when needed. Its config is
~/.config/coursesync/coursesync.yaml. This module takes its manifest of new items and ingests them
(transcribing recordings), labels weeks, and refreshes the Claude/ study folders.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from study import config
from study.config import Paths


@dataclass
class Report:
    new: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    logins: list[str] = field(default_factory=list)

    def add(self, course: str, msg: str) -> None:
        self.new.setdefault(course, []).append(msg)
        print(f"  {course}: {msg}", flush=True)

    def problem(self, msg: str) -> None:
        self.problems.append(msg)
        print(f"  ! {msg}", flush=True)

    def markdown(self) -> str:
        lines = [f"# Sync {dt.datetime.now():%Y-%m-%d %H:%M}", ""]
        if self.new:
            for c, items in sorted(self.new.items()):
                lines += [f"**{c}**", *[f"- {i}" for i in items], ""]
        else:
            lines += ["Nothing new.", ""]
        if self.logins:
            lines += ["**Needs your ETH login** (run `study sync` and log in in the window that opens): "
                      + ", ".join(self.logins), ""]
        if self.problems:
            lines += ["**Problems**", *[f"- {p}" for p in self.problems], ""]
        return "\n".join(lines)


def _ingest(paths: Paths, course: str, target: Path | str, *extra: str) -> list[dict]:
    r = subprocess.run(["uv", "run", "study", "ingest", course, str(target), "--json", *extra], cwd=paths.root,
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return [{"status": "error", "detail": (r.stderr or r.stdout)[-300:]}]


def free_key(con, course: str, key: str) -> str:
    """key, or key with b, c … after the week tag if a different source already uses it (never overwrite)."""
    def taken(k):
        return con.execute("SELECT 1 FROM sources WHERE course=? AND key=?", (course, k)).fetchone()
    if not taken(key):
        return key
    m = re.match(r"^(L\d+)(.*)$", key)
    for letter in "bcdefgh":
        k = f"{m[1]}{letter}{m[2]}" if m else f"{key}-{letter}"
        if not taken(k):
            return k
    raise ValueError(f"no free key for {course}:{key}")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")


def ingest_repos(paths: Paths, course, rep: Report) -> None:
    """Re-ingest the course's repository folders (course.yaml `repo_ingest`: [{path, key?, no_copy?}]); unchanged files are skipped."""
    changed = []
    for r in course.raw.get("repo_ingest") or []:
        target = (paths.root / Path(r["path"]).expanduser()).resolve()
        if not target.exists():
            continue
        extra = (["--key", r["key"]] if r.get("key") else []) + (["--no-copy"] if r.get("no_copy") else [])
        changed += [x["key"] for x in _ingest(paths, course.slug, target, *extra) if x.get("status") in ("added", "updated")]
    if changed:
        rep.add(course.slug, f"{len(changed)} new/updated files from the course repository")


def ingest_manifest(con, paths: Paths, items: list[dict], rep: Report) -> None:
    git_courses = set()
    for it in items:
        course, path = it["course"], Path(it["path"])
        try:
            c = config.get_course(paths, course)
        except config.ConfigError:
            rep.problem(f"{course}: not a study course (only fetched): {path.name}")
            continue
        if it["kind"] == "git":
            git_courses.add(course)
            continue
        if it["kind"] == "recording":
            key = free_key(con, course, it.get("key") or f"L{it['week']:02d}-rec")
            print(f"  {course}: transcribing {path.name} (week {it['week']})…", flush=True)
            res = _ingest(paths, course, path, "--key", key, "--lecture", str(it["week"]), "--no-copy", "--language", "en")
            label = ("tutorial recording" if it.get("tutorial") else "recording") + f" week {it['week']}"
        elif it.get("exam"):
            pe = c.dir / "past_exams" / path.name
            pe.parent.mkdir(parents=True, exist_ok=True)
            if not pe.exists():
                shutil.copy2(path, pe)
            res = _ingest(paths, course, pe, "--key", f"exam-{_slug(path.name)}")
            label = f"past exam: {path.name}"
        elif path.suffix.lower() == ".zip":  # code handouts: ingest the unpacked files, not the archive
            xdir = paths.data / "downloads" / "unzipped" / course / path.stem
            zipfile.ZipFile(path).extractall(xdir)
            res = _ingest(paths, course, xdir, "--key", f"code/{path.stem}", *(["--lecture", str(it["week"])] if it.get("week") else []))
            label = f"{it['source']}: {path.name} (unpacked)"
        else:
            key = it.get("key")
            key = free_key(con, course, Path(key).stem if key else f"{it['source']}-{_slug(path.name)}")
            extra = ["--key", key, "--no-copy"] + (["--lecture", str(it["week"])] if it.get("week") else [])
            res = _ingest(paths, course, path, *extra)
            label = f"{it['source']}: {path.name}" + (f" (week {it['week']})" if it.get("week") else "")
        statuses = {r.get("status") for r in res} or {"error"}
        if statuses & {"added", "updated", "duplicate", "unchanged"}:
            rep.add(course, label + (" (already had it)" if statuses == {"duplicate"} else ""))
        else:
            rep.problem(f"{course} {path.name}: ingest {', '.join(map(str, statuses))}: {res[0].get('detail', '') if res else ''}")
    for slug in git_courses:
        ingest_repos(paths, config.get_course(paths, slug), rep)


def autolabel(con, paths: Paths, rep: Report) -> None:
    """Give unlabelled sources a week (and key) from the course's label rules (course.yaml `labels`):
    [{match: regex on the key, week: '{1}' or '{1}+1', key: 'L{week:02}-slides' (optional)}]."""
    from study.ingest.pipeline import relabel_source
    for c in config.load_courses(paths).values():
        rules = c.raw.get("labels") or []
        if not rules:
            continue
        keys = {r[0] for r in con.execute("SELECT key FROM sources WHERE course=?", (c.slug,))}
        for (key,) in con.execute("SELECT key FROM sources WHERE course=? AND lecture IS NULL AND type IN "
                                  "('slides','exercise','script','note')", (c.slug,)).fetchall():
            for rule in rules:
                m = re.search(rule["match"], key, re.I)
                if not m:
                    continue
                spec = re.fullmatch(r"\s*\{(\d+)\}\s*(?:([+-])\s*(\d+))?\s*", str(rule.get("week", "{1}")))
                if not spec:
                    rep.problem(f"{c.slug}: bad label rule week {rule.get('week')!r}")
                    break
                n = int(m[int(spec[1])]) + (int(spec[3]) * (1 if spec[2] == "+" else -1) if spec[2] else 0)
                new = rule["key"].format(week=n) if rule.get("key") else None
                relabel_source(con, c.slug, key, new_key=new if new and new not in keys else None, lecture=n)
                rep.add(c.slug, f"{key} → week {n}")
                break


def run(paths: Paths, *, browser: bool = True, interactive: bool = True, only: list[str] | None = None) -> Report:
    """One sync at a time: the daily timer, /sync and other sessions would otherwise fetch the same items twice."""
    import fcntl
    log = paths.data / "logs"
    log.mkdir(parents=True, exist_ok=True)
    with (log / "sync.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            rep = Report()
            rep.problem("another sync is already running; this one did nothing (see data/logs/last_sync.md when it ends)")
            return rep
        return _run(paths, browser=browser, interactive=interactive, only=only)


def _run(paths: Paths, *, browser: bool, interactive: bool, only: list[str] | None) -> Report:
    import coursesync
    from coursesync.util import notify
    from study.db import connect
    con = connect(paths.db)
    rep = Report()
    cfg = coursesync.load(paths.settings.get("coursesync_config"))
    cfg.notify = False  # one notification, from here, after ingesting
    man = coursesync.run(cfg, only=only, browser=browser, interactive=interactive)
    rep.logins = man.logins_needed
    rep.problems += man.problems
    ingest_manifest(con, paths, [i.to_dict() for i in man.items], rep)
    autolabel(con, paths, rep)
    from study.pack import build
    for c in config.load_courses(paths).values():
        build(con, paths, c)
    md = rep.markdown()
    (paths.data / "logs" / "last_sync.md").write_text(md)
    with (paths.data / "logs" / "sync_history.md").open("a") as f:
        f.write(md + "\n")
    n = sum(len(v) for v in rep.new.values())
    if n or rep.logins:
        notify("study sync", (f"{n} new items in {', '.join(sorted(rep.new))}" if n else "nothing new")
               + (f"; login needed: {', '.join(rep.logins)}" if rep.logins else ""))
    return rep
