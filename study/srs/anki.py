"""Two-way Anki sync: push cards via AnkiConnect (or export an .apkg), pull review state and history back."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path

from study.srs.cards import to_anki_html

MODEL = "ETH Study (study engine)"
FIELDS = ["Front", "Back", "Source", "CardId"]
CSS = (".card{font-family:system-ui,sans-serif;font-size:19px;text-align:left;max-width:46em;margin:auto}"
       " pre,code{font-size:15px} .src{font-size:12px;color:#888;margin-top:1.2em}")
TEMPLATE = {"Name": "Card 1", "Front": "{{Front}}",
            "Back": "{{FrontSide}}<hr id=answer>{{Back}}<div class=src>{{Source}}</div>"}


class AnkiError(RuntimeError):
    pass


class AnkiConnect:
    def __init__(self, url: str = "http://127.0.0.1:8765"):
        self.url = url

    def __call__(self, action: str, **params):
        body = json.dumps({"action": action, "version": 6, "params": params}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, body), timeout=10) as r:
                resp = json.loads(r.read())
        except OSError as e:
            raise AnkiError(f"AnkiConnect not reachable at {self.url}: open Anki with the AnkiConnect add-on "
                            f"(code 2055492159). ({e})") from e
        if resp.get("error"):
            raise AnkiError(f"AnkiConnect {action}: {resp['error']}")
        return resp["result"]


def deck_name(base: str, topic: str) -> str:
    return f"{base}::{topic.replace('::', ' ')}" if topic else base


def _fields(c: dict) -> dict:
    return {"Front": to_anki_html(c["front"]), "Back": to_anki_html(c["back"]),
            "Source": c["cite"], "CardId": c["id"]}


def _tags(course: str, c: dict) -> list[str]:
    tags = [f"course::{course}", "study-engine"]
    if c.get("lecture") is not None:
        tags.append(f"week::{int(c['lecture']):02d}")
    if c.get("topic"):
        tags.append("topic::" + "_".join(c["topic"].split()))
    extra = c.get("tags") or []
    if isinstance(extra, str):
        extra = json.loads(extra)
    return tags + ["_".join(str(t).split()) for t in extra]


def ensure_model(anki: AnkiConnect) -> None:
    if MODEL not in anki("modelNames"):
        anki("createModel", modelName=MODEL, inOrderFields=FIELDS, css=CSS, cardTemplates=[TEMPLATE])


def push(con: sqlite3.Connection, course: str, base_deck: str, anki: AnkiConnect) -> dict:
    """Add new cards, update changed ones. Review history in Anki is never touched."""
    ensure_model(anki)
    rows = [dict(r) for r in con.execute("SELECT * FROM cards WHERE course=?", (course,))]
    added = updated = 0
    existing_decks = set(anki("deckNames"))
    for c in rows:
        deck = deck_name(base_deck, c["topic"])
        if deck not in existing_decks:
            anki("createDeck", deck=deck)
            existing_decks.add(deck)
        nid = c["anki_note_id"]
        if nid is None:  # maybe it exists already (DB reset): look it up by our id
            found = anki("findNotes", query=f'"CardId:{c["id"]}"')
            nid = found[0] if found else None
        if nid is None:
            nid = anki("addNote", note={"deckName": deck, "modelName": MODEL, "fields": _fields(c),
                                        "tags": _tags(course, c), "options": {"allowDuplicate": False}})
            added += 1
        elif c["pushed_hash"] != c["content_hash"]:
            anki("updateNoteFields", note={"id": nid, "fields": _fields(c)})
            updated += 1
        with con:
            con.execute("UPDATE cards SET anki_note_id=?, pushed_hash=content_hash WHERE id=?", (nid, c["id"]))
    return {"added": added, "updated": updated, "total": len(rows)}


def pull(con: sqlite3.Connection, course: str, base_deck: str, anki: AnkiConnect) -> dict:
    """Copy Anki's scheduling state and review log for this course's cards into the local DB."""
    card_ids = anki("findCards", query=f'"deck:{base_deck}" note:"{MODEL}"')
    if not card_ids:
        return {"cards": 0, "reviews": 0}
    infos = anki("cardsInfo", cards=card_ids)
    by_anki: dict[int, str] = {}
    known = {r[0] for r in con.execute("SELECT id FROM cards WHERE course=?", (course,))}
    flagged = set(anki("findCards", query=f'"deck:{base_deck}" -flag:0'))
    with con:
        for info in infos:
            cid = (info.get("fields", {}).get("CardId") or {}).get("value", "")
            if cid not in known:
                continue
            by_anki[info["cardId"]] = cid
            con.execute(
                "INSERT OR REPLACE INTO anki_state(card_id,reps,lapses,interval,ease,due,flags,queue,pulled_at)"
                " VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                (cid, info.get("reps", 0), info.get("lapses", 0), info.get("interval", 0), info.get("factor"),
                 str(info.get("due")), 1 if info["cardId"] in flagged else 0, info.get("queue")))
    reviews = anki("getReviewsOfCards", cards=[str(c) for c in by_anki])
    n = 0
    with con:
        for acid, revs in reviews.items():
            cid = by_anki.get(int(acid))
            for r in revs:
                ts = dt.datetime.fromtimestamp(r["id"] / 1000, dt.timezone.utc).isoformat()
                con.execute("INSERT OR IGNORE INTO anki_reviews(review_id,card_id,ease,ivl,ts) VALUES (?,?,?,?,?)",
                            (r["id"], cid, r["ease"], r.get("ivl"), ts))
                n += 1
    return {"cards": len(by_anki), "reviews": n, "flagged": sum(1 for c in by_anki if c in flagged)}


def struggling(con: sqlite3.Connection, course: str, limit: int = 20) -> list[dict]:
    """Leeches and flagged cards first: what Claude should help with."""
    rows = con.execute(
        "SELECT c.*, s.reps, s.lapses, s.interval, s.flags,"
        " (SELECT count(*) FROM anki_reviews r WHERE r.card_id=c.id AND r.ease=1) AS agains,"
        " (SELECT count(*) FROM anki_reviews r WHERE r.card_id=c.id) AS n_reviews"
        " FROM cards c JOIN anki_state s ON s.card_id=c.id WHERE c.course=?"
        " AND (s.flags>0 OR s.lapses>=2 OR (s.reps>=4 AND s.interval<4))"
        " ORDER BY s.flags DESC, s.lapses DESC, s.interval ASC LIMIT ?", (course, limit)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- .apkg fallback

def _stable_int(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16) + (1 << 30)


def export_apkg(con: sqlite3.Connection, course: str, base_deck: str, out: Path) -> int:
    """Write an .apkg. Notes use our card id as GUID, so re-importing updates instead of duplicating."""
    import genanki
    model = genanki.Model(_stable_int(MODEL), MODEL, fields=[{"name": f} for f in FIELDS],
                          templates=[{"name": TEMPLATE["Name"], "qfmt": TEMPLATE["Front"], "afmt": TEMPLATE["Back"]}],
                          css=CSS)
    decks: dict[str, genanki.Deck] = {}
    rows = [dict(r) for r in con.execute("SELECT * FROM cards WHERE course=?", (course,))]
    for c in rows:
        name = deck_name(base_deck, c["topic"])
        deck = decks.setdefault(name, genanki.Deck(_stable_int(name), name))
        f = _fields(c)
        deck.add_note(genanki.Note(model=model, fields=[f[k] for k in FIELDS], guid=c["id"], tags=_tags(course, c)))
    out.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(list(decks.values())).write_to_file(str(out))
    return len(rows)
