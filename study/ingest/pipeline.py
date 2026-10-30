"""Ingest orchestration: detect type, derive citation key, extract, store idempotently."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from study.config import Course, Paths
from study.ingest.base import Extracted, sha256_file
from study.ingest.code import is_code_file
from study.ingest.media import MEDIA_EXT
from study.ingest.notes import IMAGE_EXT, SUBTITLE_EXT, TEXT_EXT
from study.ingest.youtube import is_url

TYPES = ("recording", "slides", "script", "exercise", "exam", "note", "code")
COPY_LIMIT = 200 * 1024 * 1024  # bigger files (videos) are referenced in place
SKIP_DIRS = {".git", "build", "cmake-build-debug", "cmake-build-release", "__pycache__", ".venv", "node_modules",
             ".ipynb_checkpoints", ".vscode", ".idea"}

_LECTURE = re.compile(r"(?:^|[^a-z])(?:lecture|lec|vorlesung|vl|week|woche|l|w)[\s_-]*0*(\d{1,2})([a-e])?(?![a-z0-9])", re.I)
_EX_WORD = r"(?:exercises?|ex|sheet|series|serie|uebung|übung|blatt|hw|homework|assignment|ps|problem[-_ ]?set)"
_EXERCISE = re.compile(rf"(?:^|[^a-z]){_EX_WORD}[\s_-]*0*(\d{{1,2}})(?!\d)", re.I)
# a file *named* like a sheet (exercise03.pdf, sheet_4_solution.pdf), not merely mentioning one
_EXERCISE_NAME = re.compile(rf"^(?:{_EX_WORD}[\s_-]*)+0*(\d{{1,2}})[\s_-]*(solutions?|loesung|lösung)?$", re.I)


@dataclass
class Result:
    key: str
    type: str
    status: str  # added | updated | unchanged | duplicate | error
    chunks: int = 0
    images: int = 0
    detail: str = ""


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


_LEADING_NUM = re.compile(r"^0*(\d{1,2})(?:[\s_-]|$)")  # 03_invarianceStability_handout


def detect_lecture(name: str) -> int | None:
    if m := _LECTURE.search(name):
        return int(m.group(1))
    m = _LEADING_NUM.match(name)
    return int(m.group(1)) if m else None


def first_lecture(names) -> int | None:
    return next((n for name in names if (n := detect_lecture(name)) is not None), None)


def lecture_tag(name: str, lecture: int | None) -> str | None:
    """'L03', or 'L01a' for multi-part lectures like week01a / week01b."""
    if lecture is None:
        return None
    m = _LECTURE.search(name)
    part = (m.group(2) or "").lower() if m and int(m.group(1)) == lecture else ""
    return f"L{lecture:02d}{part}"


def detect_type(path: Path, course: Course) -> str:
    ext, name = path.suffix.lower(), path.name.lower()
    if course.dir / "past_exams" in path.resolve().parents:
        return "exam"
    if ext in MEDIA_EXT or ext in SUBTITLE_EXT:
        return "recording"
    if is_code_file(path):
        return "code"
    if ext in IMAGE_EXT or ext in TEXT_EXT:
        return "note"
    if ext == ".pdf":
        if re.search(r"exam|pr(ue|ü)fung|midterm|final", name):
            return "exam"
        if re.search(r"solution|loesung|lösung", name) or _EXERCISE.search(name):
            return "exercise"
        if re.search(r"script|skript|lecture[-_ ]?notes|book|notes", name):
            return "script"
        return "slides"
    raise ValueError(f"Unsupported file type: {path.name}")


def default_key(path: Path, typ: str, lecture: int | None) -> str:
    stem = slug(path.stem)
    L = lecture_tag(path.stem, lecture)
    if typ == "recording":
        return f"{L}-rec" if L else f"rec-{stem}"
    if typ in ("slides", "script"):
        return f"{L}-{typ}" if L else stem
    if typ == "exercise":
        m = _EXERCISE_NAME.match(path.stem)
        return f"ex{int(m.group(1)):02d}-sheet{'-solution' if m.group(2) else ''}" if m else stem
    if typ == "exam":
        return f"exam-{stem}"
    if typ == "note":
        return f"note-{L}-{stem}" if L else f"note-{stem}"
    return path.name  # code


def _extract(path: Path, typ: str, display: str, paths: Paths, sha: str, opts: dict) -> Extracted:
    ext = path.suffix.lower()
    if typ == "recording":
        from study.ingest import media, notes
        if ext in SUBTITLE_EXT:
            return media.extract_subtitles(notes.parse_subtitles(path))
        return media.extract_media(path, paths.cache, sha, model=opts.get("model"), device=opts.get("device", "auto"),
                                   language=opts.get("language"), frames=opts.get("frames", True))
    if ext == ".pdf":
        from study.ingest.pdf import extract_pdf
        return extract_pdf(path, paths.cache / "pages" / sha, rasterize=opts.get("rasterize", True))
    if typ == "code" or is_code_file(path):
        from study.ingest.code import extract_code
        return extract_code(path, display)
    if ext in IMAGE_EXT:
        from study.ingest.notes import extract_image_note
        return extract_image_note(path)
    if ext in TEXT_EXT:
        from study.ingest.notes import extract_text
        return extract_text(path)
    raise ValueError(f"No extractor for {path.name} as {typ}")


def _store(con: sqlite3.Connection, course: str, key: str, typ: str, lecture, path: str, stored: str | None,
           sha: str, ex: Extracted, existing_id: int | None) -> int:
    meta = json.dumps(ex.meta, ensure_ascii=False)
    with con:
        if existing_id is None:
            cur = con.execute(
                "INSERT INTO sources(course,key,type,lecture,path,stored_path,sha256,loc_kind,extent,meta)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (course, key, typ, lecture, path, stored, sha, ex.loc_kind, ex.extent, meta))
            sid = cur.lastrowid
        else:
            sid = existing_id
            con.execute("UPDATE sources SET type=?,lecture=?,path=?,stored_path=?,sha256=?,loc_kind=?,extent=?,meta=?,"
                        " ingested_at=datetime('now') WHERE id=?",
                        (typ, lecture, path, stored, sha, ex.loc_kind, ex.extent, meta, sid))
            con.execute("DELETE FROM chunks WHERE source_id=?", (sid,))
            con.execute("DELETE FROM images WHERE source_id=?", (sid,))
        con.executemany(
            "INSERT INTO chunks(source_id,ord,loc_start,loc_end,heading,text,content_hash) VALUES (?,?,?,?,?,?,?)",
            [(sid, i, c.loc_start, c.loc_end, c.heading, c.text, c.content_hash) for i, c in enumerate(ex.chunks)])
        con.executemany("INSERT INTO images(source_id,loc,kind,path) VALUES (?,?,?,?)",
                        [(sid, im.loc, im.kind, str(im.path)) for im in ex.images])
    return sid


def ingest_file(con, paths: Paths, course: Course, path: Path, *, typ=None, lecture=None, key=None,
                force=False, copy=True, display=None, origin=None, extra_meta=None, opts=None) -> Result:
    opts = opts or {}
    path = path.resolve()
    typ = typ or detect_type(path, course)
    if lecture is None:
        lecture = first_lecture([path.stem, path.parent.name])
    origin = origin or str(path)
    sha = sha256_file(path)
    if key is None:
        # auto key; if another file already owns it (two READMEs, week01a/notes...), use the path-based key
        key = default_key(path, typ, lecture)
        other = con.execute("SELECT path FROM sources WHERE course=? AND key=?", (course.slug, key)).fetchone()
        if other and other["path"] != origin and display:
            key = slug(str(Path(display).with_suffix("")).replace("/", "-"))

    row = con.execute("SELECT id, sha256, path FROM sources WHERE course=? AND key=?", (course.slug, key)).fetchone()
    if row and row["sha256"] == sha and not force:
        if row["path"] != origin:  # same content, moved file
            with con:
                con.execute("UPDATE sources SET path=? WHERE id=?", (origin, row["id"]))
        return Result(key, typ, "unchanged")
    if row and row["path"] != origin and not force:
        return Result(key, typ, "error", detail=f"key {key!r} already used by {row['path']}; pass --key to disambiguate")
    if not row and not force:
        dup = con.execute("SELECT key, path FROM sources WHERE course=? AND sha256=?", (course.slug, sha)).fetchone()
        if dup and dup["path"] == origin:  # same file, relabelled earlier: keep the user's key
            return Result(dup["key"], typ, "unchanged")
        if dup:
            return Result(key, typ, "duplicate", detail=f"same content as {dup['key']}")

    ex = _extract(path, typ, display or path.name, paths, sha, opts)
    if extra_meta:
        ex.meta.update(extra_meta)

    stored = None
    if copy and path.stat().st_size <= COPY_LIMIT and course.dir not in path.parents:
        dest = course.dir / "materials" / typ / key.replace("/", "__")
        if dest.suffix.lower() != path.suffix.lower():
            dest = dest.with_name(dest.name + path.suffix.lower())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        stored = str(dest)

    _store(con, course.slug, key, typ, lecture, origin, stored, sha, ex, row["id"] if row else None)
    return Result(key, typ, "updated" if row else "added", len(ex.chunks), len(ex.images))


def ingest(con, paths: Paths, course: Course, target: str, *, typ=None, lecture=None, key=None, force=False,
           copy=True, opts=None) -> list[Result]:
    """Ingest a file, a directory (recursively), or a YouTube URL."""
    if is_url(target):
        from study.ingest.youtube import download_audio
        audio, meta = download_audio(target, paths.cache / "youtube")
        key = key or (f"L{lecture:02d}-rec" if lecture is not None else f"yt-{meta['id']}")
        return [ingest_file(con, paths, course, audio, typ="recording", lecture=lecture, key=key, force=force,
                            copy=False, origin=target, extra_meta={"youtube": meta}, opts=opts)]

    p = Path(target).expanduser()
    if not p.exists():
        raise FileNotFoundError(target)
    if p.is_file():
        return [ingest_file(con, paths, course, p, typ=typ, lecture=lecture, key=key, force=force, copy=copy,
                            opts=opts)]

    results = []
    for f in sorted(p.rglob("*")):
        if not f.is_file() or any(part in SKIP_DIRS or part.startswith(".") for part in f.relative_to(p).parts):
            continue
        rel = f.relative_to(p).as_posix()
        try:
            ftyp = typ or detect_type(f, course)
        except ValueError:
            continue  # binaries, archives, ... silently skipped in directories
        # code keys keep their folder: ex02/vector.cpp (top-level files get the ingested dir's name)
        ckey = rel if "/" in rel else f"{p.name}/{rel}"
        if key:  # for directories, --key is a prefix, e.g. mine/ for your own solutions
            ckey = f"{key.rstrip('/')}/{rel}"
        fkey = ckey if (ftyp == "code" or key) else None
        # file name first, then the nearest lecture-like folder: demos/week02/square/main.cpp -> 2
        flec = lecture if lecture is not None else first_lecture([f.stem, *reversed(Path(p.name, rel).parts[:-1])])
        try:
            results.append(ingest_file(con, paths, course, f, typ=ftyp, lecture=flec,
                                       key=fkey, force=force, copy=copy, display=ckey, opts=opts))
        except Exception as e:  # keep going; report per file
            results.append(Result(fkey or rel, ftyp, "error", detail=f"{type(e).__name__}: {e}"))
    return results


def set_note_text(con, course: str, key: str, text: str) -> int:
    """Store Claude's transcription of an image note as its searchable text (page 1)."""
    from study.ingest.base import Chunk
    row = con.execute("SELECT id FROM sources WHERE course=? AND key=?", (course, key)).fetchone()
    if not row:
        raise KeyError(f"{course}:{key} not found")
    c = Chunk(1, 1, text.strip(), "transcribed note")
    with con:
        con.execute("DELETE FROM chunks WHERE source_id=?", (row["id"],))
        con.execute("INSERT INTO chunks(source_id,ord,loc_start,loc_end,heading,text,content_hash) VALUES (?,?,?,?,?,?,?)",
                    (row["id"], 0, 1, 1, c.heading, c.text, c.content_hash))
        con.execute("UPDATE sources SET meta=json_set(meta,'$.needs_transcription',json('false')) WHERE id=?", (row["id"],))
    return 1


def result_dict(r: Result) -> dict:
    return asdict(r)


def relabel_source(con, course: str, key: str, *, new_key=None, lecture=None, clear_lecture=False, typ=None) -> None:
    row = con.execute("SELECT id FROM sources WHERE course=? AND key=?", (course, key)).fetchone()
    if not row:
        raise KeyError(f"{course}:{key} not found")
    if typ and typ not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    if new_key and con.execute("SELECT 1 FROM sources WHERE course=? AND key=?", (course, new_key)).fetchone():
        raise ValueError(f"{course}:{new_key} already exists")
    sets, args = [], []
    if new_key:
        sets, args = sets + ["key=?"], args + [new_key]
    if clear_lecture or lecture is not None:
        sets, args = sets + ["lecture=?"], args + [None if clear_lecture else lecture]
    if typ:
        sets, args = sets + ["type=?"], args + [typ]
    if sets:
        with con:
            con.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", (*args, row["id"]))
