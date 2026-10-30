"""SQLite storage. Migrations are append-only SQL scripts tracked with PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1: sources, chunks, full-text index, images (rasterized pages, video frames)
    """
    CREATE TABLE sources(
        id          INTEGER PRIMARY KEY,
        course      TEXT NOT NULL,
        key         TEXT NOT NULL,              -- citation key, e.g. L03-slides, ex02/vector.cpp
        type        TEXT NOT NULL,              -- recording|slides|script|exercise|exam|note|code|youtube
        lecture     INTEGER,
        path        TEXT NOT NULL,              -- original path or URL
        stored_path TEXT,                       -- copy under courses/<slug>/materials (if copied)
        sha256      TEXT NOT NULL,
        loc_kind    TEXT NOT NULL,              -- t (seconds) | p (page) | L (line)
        extent      REAL NOT NULL,              -- duration in s | number of pages | number of lines
        meta        TEXT NOT NULL DEFAULT '{}',
        ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(course, key)
    );
    CREATE INDEX sources_sha ON sources(course, sha256);

    CREATE TABLE chunks(
        id           INTEGER PRIMARY KEY,
        source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        ord          INTEGER NOT NULL,
        loc_start    REAL NOT NULL,
        loc_end      REAL NOT NULL,
        heading      TEXT NOT NULL DEFAULT '',
        text         TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        UNIQUE(source_id, ord)
    );

    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        text, heading, content='chunks', content_rowid='id', tokenize='porter unicode61');
    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
    END;
    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
    END;
    CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
        INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
    END;

    CREATE TABLE images(
        id        INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        loc       REAL NOT NULL,                -- page number or timestamp (s)
        kind      TEXT NOT NULL,                -- page|frame|note
        path      TEXT NOT NULL
    );
    CREATE INDEX images_src ON images(source_id, loc);
    """,
    # 2: embeddings keyed by chunk content, so re-ingesting unchanged text never re-embeds
    """
    CREATE TABLE embeddings(
        content_hash TEXT NOT NULL,
        model        TEXT NOT NULL,
        vec          BLOB NOT NULL,             -- float32, L2-normalised
        PRIMARY KEY(content_hash, model)
    );
    CREATE INDEX chunks_hash ON chunks(content_hash);
    """,
    # 3: generated artifacts (cache keyed by input hash) + entries harvested from digests
    """
    CREATE TABLE artifacts(
        kind        TEXT NOT NULL,              -- digest | ...
        course      TEXT NOT NULL,
        target      TEXT NOT NULL,              -- e.g. lecture number as text
        input_hash  TEXT NOT NULL,              -- hash of the inputs + schema version it was built from
        path        TEXT NOT NULL,
        meta        TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY(kind, course, target)
    );
    CREATE TABLE glossary(
        course TEXT NOT NULL, lecture INTEGER NOT NULL, term TEXT NOT NULL, definition TEXT NOT NULL,
        PRIMARY KEY(course, lecture, term)
    );
    CREATE TABLE key_results(
        course TEXT NOT NULL, lecture INTEGER NOT NULL, ord INTEGER NOT NULL, name TEXT NOT NULL,
        statement TEXT NOT NULL,
        PRIMARY KEY(course, lecture, ord)
    );
    """,
    # 4: question bank + attempts (practice and grading)
    """
    CREATE TABLE questions(
        id          TEXT PRIMARY KEY,
        course      TEXT NOT NULL,
        title       TEXT NOT NULL,
        topic       TEXT NOT NULL DEFAULT '',
        lecture     INTEGER,
        kind        TEXT NOT NULL,
        difficulty  TEXT NOT NULL,
        points      REAL NOT NULL,
        source      TEXT NOT NULL,              -- generated | past-exam:<key> | digest
        grading     TEXT NOT NULL,              -- rubric | io | unit
        dir         TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        added_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX questions_course ON questions(course, topic);
    CREATE TABLE attempts(
        id          INTEGER PRIMARY KEY,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        session     TEXT NOT NULL,
        ts          TEXT NOT NULL DEFAULT (datetime('now')),
        score       REAL NOT NULL,              -- 0..1
        points      REAL,
        max_points  REAL,
        steps       TEXT NOT NULL DEFAULT '[]', -- [{step, points, max, comment}]
        feedback    TEXT NOT NULL DEFAULT '',   -- path to feedback markdown
        tests       TEXT NOT NULL DEFAULT '{}'  -- runner summary for code questions
    );
    CREATE INDEX attempts_q ON attempts(question_id, ts);
    """,
    # 5: spaced repetition: flashcards (mirrored to Anki), Anki review state, FSRS state for bank questions
    """
    CREATE TABLE cards(
        id           TEXT PRIMARY KEY,          -- stable; also the Anki note GUID
        course       TEXT NOT NULL,
        topic        TEXT NOT NULL DEFAULT '',
        lecture      INTEGER,
        front        TEXT NOT NULL,
        back         TEXT NOT NULL,
        cite         TEXT NOT NULL DEFAULT '',
        tags         TEXT NOT NULL DEFAULT '[]',
        content_hash TEXT NOT NULL,
        anki_note_id INTEGER,
        pushed_hash  TEXT,                      -- content_hash last sent to Anki
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX cards_course ON cards(course, topic);
    CREATE TABLE anki_state(
        card_id   TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
        reps      INTEGER NOT NULL DEFAULT 0,
        lapses    INTEGER NOT NULL DEFAULT 0,
        interval  INTEGER NOT NULL DEFAULT 0,   -- days (negative = seconds, learning)
        ease      INTEGER,                      -- factor (permille) or FSRS difficulty proxy
        due       TEXT,
        flags     INTEGER NOT NULL DEFAULT 0,
        queue     INTEGER,
        pulled_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE anki_reviews(
        review_id INTEGER PRIMARY KEY,          -- Anki revlog id (ms timestamp)
        card_id   TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
        ease      INTEGER NOT NULL,             -- 1 again, 2 hard, 3 good, 4 easy
        ivl       INTEGER,
        ts        TEXT NOT NULL
    );
    CREATE TABLE question_srs(
        question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
        fsrs        TEXT NOT NULL,              -- py-fsrs Card.to_json()
        due         TEXT NOT NULL
    );
    """,
    # 6: ledger of everything fetched from online sources, so a weekly sync only downloads what's new
    """
    CREATE TABLE fetched(
        source_url TEXT PRIMARY KEY,            -- tobira mp4 / zoom share link / moodle pluginfile / website url
        course     TEXT NOT NULL,
        kind       TEXT NOT NULL,               -- tobira | zoom | moodle | web | youtube
        local_path TEXT NOT NULL,
        key        TEXT,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 7: interactive teaching sessions (/teach): lesson plan, position, struggles, and check-question results
    """
    CREATE TABLE teach_sessions(
        course     TEXT NOT NULL,
        target     TEXT NOT NULL,               -- W03 (a week) or topic-<slug>
        title      TEXT NOT NULL,
        plan       TEXT NOT NULL,               -- JSON list of {name, topic, cites}
        position   INTEGER NOT NULL DEFAULT 0,  -- index of the next concept to teach
        status     TEXT NOT NULL DEFAULT 'active',  -- active | done
        struggles  TEXT NOT NULL DEFAULT '[]',  -- JSON list of {concept, note}
        claude_session TEXT,                    -- Claude Code session id, so the GUI can resume the chat
        started    TEXT NOT NULL DEFAULT (datetime('now')),
        updated    TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (course, target)
    );
    CREATE TABLE teach_checks(
        id      INTEGER PRIMARY KEY,
        course  TEXT NOT NULL,
        target  TEXT NOT NULL,
        concept TEXT NOT NULL,
        topic   TEXT,
        score   REAL NOT NULL,                  -- 0 wrong, 0.5 partly, 1 right
        ts      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
        con.executescript(f"BEGIN; {script}; PRAGMA user_version = {i}; COMMIT;")
