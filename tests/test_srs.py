import datetime as dt
import sqlite3
import zipfile

import pytest

from study import config
from study.ingest.pipeline import ingest
from study.srs import anki as A
from study.srs.cards import add_cards, card_id, edit_card, to_anki_html
from study.srs.fsrs_q import rating_for, review
from study.srs.mastery import mastery

CARDS = [
    {"front": "What does `std::move` do?", "back": "Casts to an rvalue reference $T\\&\\&$.",
     "cite": "[@demo:L03-slides#p2]", "topic": "Move semantics", "lecture": 3},
    {"front": "When does a move actually happen?", "back": "When a move ctor/assignment runs.",
     "cite": "[@demo:L03-rec#t=00:01:35]", "topic": "Move semantics", "lecture": 3},
]


class FakeAnki:
    """Just enough of AnkiConnect's behaviour for push/pull."""

    def __init__(self):
        self.notes, self.decks, self.models, self.next_id = {}, {"Default"}, set(), 1000
        self.card_state, self.reviews, self.flags = {}, {}, set()

    def __call__(self, action, **p):
        if action == "modelNames":
            return list(self.models)
        if action == "createModel":
            self.models.add(p["modelName"])
            return None
        if action == "deckNames":
            return list(self.decks)
        if action == "createDeck":
            self.decks.add(p["deck"])
            return 1
        if action == "findNotes":
            cid = p["query"].split("CardId:")[1].strip('"')
            return [n for n, v in self.notes.items() if v["fields"]["CardId"] == cid]
        if action == "addNote":
            self.next_id += 1
            self.notes[self.next_id] = {**p["note"]}
            self.card_state[self.next_id] = {"reps": 0, "lapses": 0, "interval": 0}
            return self.next_id
        if action == "updateNoteFields":
            self.notes[p["note"]["id"]]["fields"] = p["note"]["fields"]
            return None
        if action == "findCards":
            ids = list(self.notes)
            return [i for i in ids if i in self.flags] if "-flag:0" in p["query"] else ids
        if action == "cardsInfo":
            return [{"cardId": i, "fields": {k: {"value": v} for k, v in self.notes[i]["fields"].items()},
                     "factor": 2500, "due": 5, "queue": 2, **self.card_state[i]} for i in p["cards"]]
        if action == "getReviewsOfCards":
            return {c: self.reviews.get(int(c), []) for c in p["cards"]}
        raise AssertionError(f"unexpected action {action}")


@pytest.fixture
def srs(project, materials):
    paths, course, con = project
    ingest(con, paths, course, str(materials), copy=False)
    return paths, course, con


def test_cards_need_valid_citations_and_are_idempotent(srs):
    _, _, con = srs
    bad = [{"front": "x?", "back": "y"}, {"front": "z?", "back": "w", "cite": "[@demo:L03-slides#p99]"}]
    res = add_cards(con, "demo", CARDS + bad)
    assert res["added"] == 2 and len(res["errors"]) == 2
    assert add_cards(con, "demo", CARDS)["unchanged"] == 2
    changed = [dict(CARDS[0], back="A cast. Nothing moves yet.")] + CARDS[1:]
    res = add_cards(con, "demo", changed)
    assert res["updated"] == 1 and res["added"] == 0  # same front -> same id -> same Anki note


def test_push_update_pull_roundtrip(srs):
    _, _, con = srs
    add_cards(con, "demo", CARDS)
    fake = FakeAnki()
    assert A.push(con, "demo", "ETH::DEMO", fake) == {"added": 2, "updated": 0, "total": 2}
    assert "ETH::DEMO::Move semantics" in fake.decks
    assert A.push(con, "demo", "ETH::DEMO", fake)["added"] == 0  # nothing new
    edit_card(con, card_id("demo", CARDS[0]["front"]), back="Edited")
    assert A.push(con, "demo", "ETH::DEMO", fake)["updated"] == 1

    nid = next(iter(fake.notes))
    fake.card_state[nid] = {"reps": 6, "lapses": 3, "interval": 1}
    fake.flags.add(nid)
    now_ms = int(dt.datetime.now().timestamp() * 1000)
    fake.reviews[nid] = [{"id": now_ms - 1000, "ease": 1, "ivl": 1}, {"id": now_ms, "ease": 3, "ivl": 2}]
    res = A.pull(con, "demo", "ETH::DEMO", fake)
    assert res == {"cards": 2, "reviews": 2, "flagged": 1}
    assert A.pull(con, "demo", "ETH::DEMO", fake)["cards"] == 2  # idempotent
    assert con.execute("SELECT count(*) FROM anki_reviews").fetchone()[0] == 2
    stuck = A.struggling(con, "demo")
    assert len(stuck) == 1 and stuck[0]["flags"] == 1 and stuck[0]["lapses"] == 3


def test_apkg_has_stable_guids(srs, tmp_path):
    _, _, con = srs
    add_cards(con, "demo", CARDS)
    for name in ("a.apkg", "b.apkg"):
        assert A.export_apkg(con, "demo", "ETH::DEMO", tmp_path / name) == 2

    def guids(p):
        with zipfile.ZipFile(p) as z:
            z.extract("collection.anki2", tmp_path / p.stem)
        return sorted(r[0] for r in sqlite3.connect(tmp_path / p.stem / "collection.anki2").execute("SELECT guid FROM notes"))
    assert guids(tmp_path / "a.apkg") == guids(tmp_path / "b.apkg") == sorted(card_id("demo", c["front"]) for c in CARDS)


def test_math_and_markdown_rendering():
    html = to_anki_html("Energy $E = \\frac{1}{2}mv^2$ and **bold**\n\n$$\\dot V \\le 0$$ costs $5")
    assert "\\(E = \\frac{1}{2}mv^2\\)" in html and "\\[\\dot V \\le 0\\]" in html and "<strong>bold</strong>" in html
    assert "$5" in html  # a lone dollar sign is not math


def test_fsrs_intervals_grow_on_success_and_reset_on_failure(srs):
    from tests.test_questions import rubric_q
    from study.practice.questions import add
    paths, course, con = srs
    qid, _ = add(con, course, rubric_q(paths.root.parent), paths.data / "build")
    t0 = dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.timezone.utc)
    d1 = review(con, qid, 1.0, t0)
    d2 = review(con, qid, 1.0, d1)
    assert (d2 - d1) > (d1 - t0)  # successful recalls -> growing intervals
    d3 = review(con, qid, 0.1, d2)
    assert (d3 - d2) < (d2 - d1)  # a failure brings it back soon
    assert rating_for(0.2).name == "Again" and rating_for(0.95).name == "Easy"


def test_mastery_combines_and_ranks(srs):
    from tests.test_questions import rubric_q
    from study.practice.questions import add, log_attempt
    paths, course, con = srs
    (course.dir / "course.yaml").write_text((course.dir / "course.yaml").read_text() + "\n" + config.MANAGED_MARKER +
                                            "\ntopics: [Move semantics, Templates]\n")
    course = config.load_course(course.dir)
    qid, _ = add(con, course, rubric_q(paths.root.parent), paths.data / "build")
    log_attempt(con, qid, "s1", 0.5)
    rows = {r["topic"]: r for r in mastery(con, course)}
    assert rows["Move semantics"]["mastery"] == pytest.approx(0.5)
    assert rows["Templates"]["mastery"] is None  # approved topic without evidence is still listed
    assert list(rows)[-1] == "Templates"  # topics without evidence go last
