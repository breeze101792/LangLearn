"""Tests for the vocab service."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture for tests that
    read `fresh` for documentation purposes. The autouse fixture in
    conftest.py already sets up the data dir + db schema and clears
    module-level state — see `tests/conftest.py`."""
    return clean_state


def _seed_vocab(fresh, *, word="casa", lang="es", glossary="house"):
    from backend.services import vocab as v
    return v.add_vocab(
        user_id=1, language=lang, word=word, source="wordnet",
        glossary=glossary, pos="noun",
    )


def test_add_vocab_creates_row(fresh):
    res = _seed_vocab(fresh)
    assert res["created"] is True
    assert res["word"] == "casa"


def test_add_vocab_dedupes(fresh):
    _seed_vocab(fresh)
    res = _seed_vocab(fresh)
    assert res["created"] is False


def test_list_vocab_paginated(fresh):
    for w in ["a", "b", "c"]:
        _seed_vocab(fresh, word=w)
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="es", limit=2)
    assert len(items) == 2


def test_delete_and_restore(fresh):
    res = _seed_vocab(fresh)
    vocab_id = res["id"]
    from backend.services import vocab as v
    del_res = v.delete_vocab(user_id=1, vocab_id=vocab_id)
    assert del_res["deleted_id"] == vocab_id
    assert "undo_token" in del_res
    items = v.list_vocab(user_id=1, language="es")
    assert items == []
    restored = v.restore_vocab(user_id=1, undo_token=del_res["undo_token"])
    assert restored["restored_id"] == vocab_id
    items = v.list_vocab(user_id=1, language="es")
    assert len(items) == 1


def test_review_next_returns_due(fresh):
    res = _seed_vocab(fresh, word="alpha")
    _seed_vocab(fresh, word="beta")
    from backend.services import vocab as v
    items = v.review_next(user_id=1, language="es", n=10)
    assert len(items) == 2
    assert items[0]["word"] in ("alpha", "beta")


def test_apply_grade_promotes(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    out = v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="easy")
    assert out["leitner_box"] == 2
    out = v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="hard")
    assert out["leitner_box"] == 1


def test_apply_grade_invalid(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="ok")


def test_auto_add_from_lookup(fresh):
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="gato", language="es", source="wordnet",
        senses=[Sense(pos="noun",
                      definitions=[Definition(glossary="cat", example="Mi gato.")],
                      explanations={"primary": "cat", "secondary": "猫"})],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is True
    items = v.list_vocab(user_id=1, language="es")
    assert any(i["word"] == "gato" for i in items)


def test_auto_add_disabled(fresh):
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="gato", language="es", source="wordnet",
        senses=[Sense(pos="noun",
                      definitions=[Definition(glossary="cat")])],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=False)
    assert added is False
    items = v.list_vocab(user_id=1, language="es")
    assert items == []


def test_auto_add_multi_sense_creates_one_row(fresh):
    """Regression: a multi-sense lookup must produce one vocab row, not one per sense.

    Otherwise the review session shows the same word N times in a row, which
    defeats spaced repetition.
    """
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="test", language="en", source="wordnet",
        senses=[
            Sense(pos="noun", definitions=[Definition(glossary="a procedure",
                                                     example="run a test")]),
            Sense(pos="verb", definitions=[Definition(glossary="to evaluate",
                                                     example="test the water")]),
            Sense(pos="noun", definitions=[Definition(glossary="an exam",
                                                     example="take a test")]),
        ],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is True
    items = v.list_vocab(user_id=1, language="en")
    assert len(items) == 1, f"expected 1 vocab row, got {len(items)}"
    assert items[0]["word"] == "test"

    next_items = v.review_next(user_id=1, language="en", n=10)
    assert len(next_items) == 1


def test_auto_add_re_lookup_updates_in_place(fresh):
    """Looking up the same word again must update the row, not create a duplicate."""
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    first = WordEntry(
        word="test", language="en", source="wordnet",
        senses=[Sense(pos="noun", definitions=[Definition(glossary="old gloss")])],
    )
    v.auto_add_from_lookup(user_id=1, entry=first, auto_add_enabled=True)

    second = WordEntry(
        word="test", language="en", source="llm",
        senses=[Sense(pos="noun", definitions=[Definition(glossary="new gloss",
                                                         example="a fresh example")])],
    )
    res = v.auto_add_from_lookup(user_id=1, entry=second, auto_add_enabled=True)
    assert res is False  # updated, not created

    items = v.list_vocab(user_id=1, language="en")
    assert len(items) == 1
    assert items[0]["glossary"] == "new gloss"
    assert items[0]["source"] == "llm"


def test_review_status(fresh):
    _seed_vocab(fresh, word="a")
    _seed_vocab(fresh, word="b")
    from backend.services import vocab as v
    st = v.review_status(user_id=1, language="es")
    assert st["due"] == 2
    assert st["by_box"][1] == 2


# --- list_vocab box filter --------------------------------------------------

def test_list_vocab_filters_by_box(fresh):
    """list_vocab(box=N) must only return items at that Leitner level."""
    a = _seed_vocab(fresh, word="a")
    b = _seed_vocab(fresh, word="b")
    _seed_vocab(fresh, word="c")
    from backend.services import vocab as v
    v.set_box(user_id=1, vocab_id=b["id"], box=3)
    out1 = v.list_vocab(user_id=1, language="es", box=1)
    assert {i["word"] for i in out1} == {"a", "c"}
    out3 = v.list_vocab(user_id=1, language="es", box=3)
    assert {i["word"] for i in out3} == {"b"}
    out_all = v.list_vocab(user_id=1, language="es")
    assert {i["word"] for i in out_all} == {"a", "b", "c"}


def test_list_vocab_invalid_box_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.list_vocab(user_id=1, language="es", box=0)
    with pytest.raises(ValueError):
        v.list_vocab(user_id=1, language="es", box=6)


def test_list_vocab_box_range(fresh):
    """list_vocab(box_min, box_max) restricts to a range of boxes."""
    from backend.services import vocab as v
    a = _seed_vocab(fresh, word="a")
    b = _seed_vocab(fresh, word="b")
    c = _seed_vocab(fresh, word="c")
    v.set_box(user_id=1, vocab_id=a["id"], box=1)
    v.set_box(user_id=1, vocab_id=b["id"], box=3)
    v.set_box(user_id=1, vocab_id=c["id"], box=5)
    # Reviewed = boxes 2-5.
    out = v.list_vocab(user_id=1, language="es", box_min=2, box_max=5)
    assert {i["word"] for i in out} == {"b", "c"}
    # New = box 1 only.
    out_new = v.list_vocab(user_id=1, language="es", box_min=1, box_max=1)
    assert {i["word"] for i in out_new} == {"a"}
    assert v.count_vocab(user_id=1, language="es", box_min=2, box_max=5) == 2


def test_list_vocab_box_range_invalid(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.list_vocab(user_id=1, language="es", box_min=0)
    with pytest.raises(ValueError):
        v.list_vocab(user_id=1, language="es", box_max=6)


# --- count_vocab -----------------------------------------------------------

def test_count_vocab_total(fresh):
    for w in ["a", "b", "c", "d"]:
        _seed_vocab(fresh, word=w)
    from backend.services import vocab as v
    assert v.count_vocab(user_id=1, language="es") == 4


def test_count_vocab_filtered_by_box(fresh):
    a = _seed_vocab(fresh, word="a")
    b = _seed_vocab(fresh, word="b")
    _seed_vocab(fresh, word="c")
    from backend.services import vocab as v
    v.set_box(user_id=1, vocab_id=b["id"], box=3)
    assert v.count_vocab(user_id=1, language="es", box=1) == 2
    assert v.count_vocab(user_id=1, language="es", box=3) == 1
    assert v.count_vocab(user_id=1, language="es", box=5) == 0


def test_count_vocab_invalid_box(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.count_vocab(user_id=1, language="es", box=9)


# --- set_box service -------------------------------------------------------

def test_set_box_updates_level_and_reschedules(fresh):
    res = _seed_vocab(fresh, word="hola")
    from backend.services import vocab as v
    out = v.set_box(user_id=1, vocab_id=res["id"], box=4)
    assert out["leitner_box"] == 4
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["leitner_box"] == 4
    assert items[0]["next_due"] > "2000-01-01"  # rescheduled into the future


def test_set_box_invalid_value_raises(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.set_box(user_id=1, vocab_id=res["id"], box=0)
    with pytest.raises(ValueError):
        v.set_box(user_id=1, vocab_id=res["id"], box=99)


def test_set_box_unknown_id_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(LookupError):
        v.set_box(user_id=1, vocab_id=9999, box=2)


# --- HTTP endpoints --------------------------------------------------------

def test_list_vocab_endpoint_box_filter(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    a = _seed_vocab(fresh, word="alpha", lang="en")
    _seed_vocab(fresh, word="beta", lang="en")
    _seed_vocab(fresh, word="gamma", lang="en")
    from backend.services import vocab as v
    v.set_box(user_id=1, vocab_id=a["id"], box=5)
    r = client.get("/api/vocab?lang=en&box=5")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["box"] == 5
    assert {i["word"] for i in body["data"]["items"]} == {"alpha"}
    assert body["data"]["total"] == 1
    assert body["data"]["by_box"]["5"] == 1


def test_list_vocab_endpoint_pagination(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    for w in ["a", "b", "c", "d", "e"]:
        _seed_vocab(fresh, word=w, lang="en")
    r1 = client.get("/api/vocab?lang=en&limit=2&offset=0")
    r2 = client.get("/api/vocab?lang=en&limit=2&offset=2")
    r3 = client.get("/api/vocab?lang=en&limit=2&offset=4")
    assert r1.status_code == 200
    body1 = r1.get_json()["data"]
    body2 = r2.get_json()["data"]
    body3 = r3.get_json()["data"]
    assert body1["total"] == 5
    assert body2["total"] == 5
    assert len(body1["items"]) == 2
    assert len(body2["items"]) == 2
    assert len(body3["items"]) == 1  # last partial page
    assert body1["limit"] == 2
    assert body1["offset"] == 0
    assert body3["offset"] == 4
    # No overlap between pages.
    words = [i["word"] for i in body1["items"] + body2["items"] + body3["items"]]
    assert len(set(words)) == 5


def test_list_vocab_endpoint_box_invalid(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab?lang=en&box=abc")
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_box"


def test_list_vocab_endpoint_box_range(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    a = _seed_vocab(fresh, word="alpha", lang="en")
    _seed_vocab(fresh, word="beta", lang="en")
    from backend.services import vocab as v
    v.set_box(user_id=1, vocab_id=a["id"], box=4)
    r = client.get("/api/vocab?lang=en&box_min=2&box_max=5")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert {i["word"] for i in body["items"]} == {"alpha"}
    assert body["total"] == 1
    assert body["box_min"] == 2
    assert body["box_max"] == 5


def test_patch_vocab_endpoint_sets_level(fresh):
    res = _seed_vocab(fresh, word="hola")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.patch(f"/api/vocab/{res['id']}", json={"leitner_box": 3})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["leitner_box"] == 3
    # And the row is actually updated.
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["leitner_box"] == 3


def test_patch_vocab_endpoint_missing_box(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.patch(f"/api/vocab/{res['id']}", json={})
    assert r.status_code == 400


def test_patch_vocab_endpoint_invalid_box(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.patch(f"/api/vocab/{res['id']}", json={"leitner_box": 9})
    assert r.status_code == 400


def test_patch_vocab_endpoint_unknown_id(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.patch("/api/vocab/9999", json={"leitner_box": 2})
    assert r.status_code == 404


# --- find_vocab_box service + GET /api/vocab/lookup -----------------------

def test_find_vocab_box_returns_none_when_missing(fresh):
    from backend.services import vocab as v
    assert v.find_vocab_box(user_id=1, language="en", word="nope") is None


def test_find_vocab_box_returns_id_and_box(fresh):
    res = _seed_vocab(fresh, word="hola", lang="es")
    from backend.services import vocab as v
    out = v.find_vocab_box(user_id=1, language="es", word="hola")
    assert out == {"id": res["id"], "leitner_box": 1}


def test_find_vocab_box_reflects_promotion(fresh):
    res = _seed_vocab(fresh, word="hola", lang="es")
    from backend.services import vocab as v
    v.set_box(user_id=1, vocab_id=res["id"], box=4)
    out = v.find_vocab_box(user_id=1, language="es", word="hola")
    assert out == {"id": res["id"], "leitner_box": 4}


def test_find_vocab_box_invalid_lang_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.find_vocab_box(user_id=1, language="!!", word="x")


def test_lookup_vocab_endpoint_misses(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/lookup?lang=en&word=ghost")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["in_vocab"] is False
    assert body["data"]["leitner_box"] is None


def test_lookup_vocab_endpoint_hit(fresh):
    res = _seed_vocab(fresh, word="hola", lang="es")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/lookup?lang=es&word=hola")
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["in_vocab"] is True
    assert body["data"]["leitner_box"] == 1
    assert body["data"]["vocab_id"] == res["id"]


def test_lookup_vocab_endpoint_bad_lang(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/lookup?lang=!!&word=hola")
    assert r.status_code == 400


def test_lookup_vocab_endpoint_missing_word(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/lookup?lang=en")
    assert r.status_code == 400


# --- POST /api/vocab/add-from-entry --------------------------------------

def test_add_from_entry_creates_row_at_box_1(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "banana",
        "source": "llm", "pos": "noun",
        "glossary": "a yellow fruit",
        "example": "I ate a banana.",
        "explanation_primary": "fruit",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["created"] is True
    assert body["data"]["leitner_box"] == 1
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="en")
    assert items[0]["word"] == "banana"
    assert items[0]["leitner_box"] == 1


def test_add_from_entry_refreshes_existing_row(fresh):
    _seed_vocab(fresh, word="banana", lang="en", glossary="old")
    # Promote to box 3 first.
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="en")
    v.set_box(user_id=1, vocab_id=items[0]["id"], box=3)

    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "banana",
        "source": "wordnet", "glossary": "new",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["created"] is False  # refresh, not create
    assert body["data"]["leitner_box"] == 3  # box preserved
    items = v.list_vocab(user_id=1, language="en")
    assert items[0]["glossary"] == "new"
    assert items[0]["leitner_box"] == 3


def test_add_from_entry_rejects_bad_source(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "x", "source": "google", "glossary": "y",
    })
    assert r.status_code == 400


def test_add_from_entry_requires_glossary(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "x", "source": "llm",
    })
    assert r.status_code == 400


# --- dictionary lookup payload includes in_vocab -------------------------

def test_dictionary_lookup_payload_includes_vocab_state(fresh):
    """The dictionary /lookup response must carry in_vocab + leitner_box so
    the card can render the Source row without a second round-trip."""
    _seed_vocab(fresh, word="banana", lang="en")
    from backend.app import create_app
    from backend.services.dictionaries import registry, wordnet as wordnet_svc

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "banana"})
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["in_vocab"] is True
    assert body["leitner_box"] == 1
    assert isinstance(body["vocab_id"], int)


def test_dictionary_lookup_payload_in_vocab_false_when_missing(fresh):
    """For a word that WordNet can't find, the dictionary /lookup response
    must report in_vocab=false so the card can render the Add button."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "zzznotaword"})
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["in_vocab"] is False
    assert body["leitner_box"] is None
    assert body["vocab_id"] is None


# --- input validation on add_vocab ----------------------------------------

def test_add_vocab_rejects_invalid_language(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="language"):
        v.add_vocab(user_id=1, language="ENG123", word="x", source="user",
                    glossary="g")


def test_add_vocab_rejects_empty_word(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="word"):
        v.add_vocab(user_id=1, language="es", word="", source="user", glossary="g")


def test_add_vocab_rejects_whitespace_word(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="word"):
        v.add_vocab(user_id=1, language="es", word="   ", source="user",
                    glossary="g")


def test_add_vocab_rejects_non_string_word(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="word"):
        v.add_vocab(user_id=1, language="es", word=123, source="user",
                    glossary="g")


def test_add_vocab_rejects_empty_glossary(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="glossary"):
        v.add_vocab(user_id=1, language="es", word="x", source="user",
                    glossary="")


def test_add_vocab_rejects_invalid_source(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="source"):
        v.add_vocab(user_id=1, language="es", word="x", source="google",
                    glossary="g")


def test_add_vocab_strips_and_truncates_word(fresh):
    from backend.services import vocab as v
    long_word = "x" * 300
    res = v.add_vocab(user_id=1, language="es", word=f"  {long_word}  ",
                      source="user", glossary="g")
    assert len(res["word"]) == 200


def test_add_vocab_strips_and_truncates_glossary(fresh):
    from backend.services import vocab as v
    long = "g" * 1500
    res = v.add_vocab(user_id=1, language="es", word="x", source="user",
                      glossary=f"  {long}  ")
    items = v.list_vocab(user_id=1, language="es")
    assert len(items[0]["glossary"]) == 1000


def test_add_vocab_truncates_pos(fresh):
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="x", source="user",
                      glossary="g", pos="p" * 100)
    items = v.list_vocab(user_id=1, language="es")
    assert len(items[0]["pos"]) == 32


def test_add_vocab_negative_sense_idx_coerced_to_zero(fresh):
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="x", source="user",
                      glossary="g", sense_idx=-5)
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["sense_idx"] == 0


def test_add_vocab_strips_empty_example_to_none(fresh):
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="x", source="user",
                      glossary="g", example="   ")
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["example"] is None


# --- delete / undo restore edge cases -------------------------------------

def test_delete_vocab_unknown_id_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(LookupError):
        v.delete_vocab(user_id=1, vocab_id=9999)


def test_restore_vocab_unknown_token_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(LookupError):
        v.restore_vocab(user_id=1, undo_token="bogus-token")


def test_delete_vocab_returns_undo_ttl_in_response(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    out = v.delete_vocab(user_id=1, vocab_id=res["id"])
    assert out["ttl_seconds"] == v.UNDO_TTL_SECONDS


def test_restore_vocab_round_trip_preserves_box(fresh):
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="hola", source="user",
                      glossary="hi")
    v.set_box(user_id=1, vocab_id=res["id"], box=4)
    del_res = v.delete_vocab(user_id=1, vocab_id=res["id"])
    v.restore_vocab(user_id=1, undo_token=del_res["undo_token"])
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["leitner_box"] == 4


# --- list_vocab ordering and limits ---------------------------------------

def test_list_vocab_orders_by_added_at_desc(fresh):
    """Newer added_at first. Use explicit added_at to avoid second-resolution
    collisions when inserts happen in the same tick."""
    from backend.services import vocab as v
    from backend.db import transaction
    a = v.add_vocab(user_id=1, language="es", word="first", source="user",
                    glossary="g")
    b = v.add_vocab(user_id=1, language="es", word="second", source="user",
                    glossary="g")
    c = v.add_vocab(user_id=1, language="es", word="third", source="user",
                    glossary="g")
    with transaction() as conn:
        conn.execute("UPDATE vocab_items SET added_at='2020-01-01' WHERE id=?",
                     (a["id"],))
        conn.execute("UPDATE vocab_items SET added_at='2021-01-01' WHERE id=?",
                     (b["id"],))
        conn.execute("UPDATE vocab_items SET added_at='2022-01-01' WHERE id=?",
                     (c["id"],))
    items = v.list_vocab(user_id=1, language="es")
    assert [i["word"] for i in items] == ["third", "second", "first"]


def test_list_vocab_clamps_huge_limit(fresh):
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="es", limit=99999)
    # The function clamps to <=500; we only care it didn't raise.
    assert items == []


def test_list_vocab_clamps_negative_limit(fresh):
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="es", limit=-5)
    # Negative limit gets coerced to 1, not a crash.
    assert items == []


def test_list_vocab_clamps_negative_offset(fresh):
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="es", offset=-10)
    assert items == []


def test_list_vocab_other_user_isolated(fresh):
    _seed_vocab(fresh, word="a")
    from backend.services import vocab as v
    # user_id=2 is never seeded but the FK is loose in test; ensure we can
    # still query without seeing user 1's data.
    items = v.list_vocab(user_id=2, language="es")
    assert items == []


def test_list_vocab_other_language_isolated(fresh):
    _seed_vocab(fresh, word="casa", lang="es")
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="fr")
    assert items == []


def test_count_vocab_invalid_language(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.count_vocab(user_id=1, language="!!")


def test_list_vocab_invalid_language(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.list_vocab(user_id=1, language="!!")


# --- review_next edge cases -----------------------------------------------

def test_review_next_invalid_language(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.review_next(user_id=1, language="!!", n=10)


def test_review_next_excludes_future_due(fresh):
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="x", source="user",
                      glossary="g")
    # Push into box 5 -> 30 days out, so it shouldn't be due.
    v.set_box(user_id=1, vocab_id=res["id"], box=5)
    items = v.review_next(user_id=1, language="es", n=10)
    assert items == []


def test_review_next_includes_overdue_items(fresh):
    from backend.services import vocab as v
    from backend.db import transaction
    res = v.add_vocab(user_id=1, language="es", word="x", source="user",
                      glossary="g")
    # Force this row to be overdue.
    with transaction() as conn:
        conn.execute(
            "UPDATE vocab_items SET next_due='1999-01-01 00:00:00' WHERE id=?",
            (res["id"],),
        )
    items = v.review_next(user_id=1, language="es", n=10)
    assert any(i["word"] == "x" for i in items)


def test_review_next_n_is_clamped_to_50(fresh):
    from backend.services import vocab as v
    # Add a due item, then request a huge N. We just check it doesn't crash.
    v.add_vocab(user_id=1, language="es", word="x", source="user", glossary="g")
    items = v.review_next(user_id=1, language="es", n=999)
    assert isinstance(items, list)


def test_review_next_box_filters_single_box(fresh):
    """review_next(box=N) must only return items at that Leitner box,
    regardless of due date."""
    from backend.services import vocab as v
    a = v.add_vocab(user_id=1, language="es", word="a", source="user", glossary="g")
    b = v.add_vocab(user_id=1, language="es", word="b", source="user", glossary="g")
    v.set_box(user_id=1, vocab_id=a["id"], box=3)
    # b is box 1 and due; a is box 3 and not due. box=3 must return only a.
    items = v.review_next(user_id=1, language="es", n=10, box=3)
    assert [i["word"] for i in items] == ["a"]
    items1 = v.review_next(user_id=1, language="es", n=10, box=1)
    assert [i["word"] for i in items1] == ["b"]


def test_review_next_box_zero_returns_all_boxes(fresh):
    """review_next(box=0) must pull from every box regardless of due date."""
    from backend.services import vocab as v
    a = v.add_vocab(user_id=1, language="es", word="a", source="user", glossary="g")
    b = v.add_vocab(user_id=1, language="es", word="b", source="user", glossary="g")
    v.set_box(user_id=1, vocab_id=a["id"], box=5)  # not due (30 days out)
    items = v.review_next(user_id=1, language="es", n=10, box=0)
    assert {i["word"] for i in items} == {"a", "b"}


def test_review_next_box_invalid_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.review_next(user_id=1, language="es", n=10, box=6)
    with pytest.raises(ValueError):
        v.review_next(user_id=1, language="es", n=10, box=-1)


def test_review_next_shuffle_respects_n_and_set(fresh):
    """shuffle=True must return the same set of words but not exceed n."""
    from backend.services import vocab as v
    for w in ["a", "b", "c", "d", "e"]:
        v.add_vocab(user_id=1, language="es", word=w, source="user", glossary="g")
    got = v.review_next(user_id=1, language="es", n=3, shuffle=True)
    assert len(got) == 3
    # Every returned word is in the eligible pool.
    pool = v.review_next(user_id=1, language="es", n=50, shuffle=False)
    pool_words = {i["word"] for i in pool}
    assert all(i["word"] in pool_words for i in got)


def test_review_next_shuffle_changes_order(fresh):
    """Two shuffled draws of the full pool should (almost always) differ."""
    from backend.services import vocab as v
    for w in ["a", "b", "c", "d", "e", "f", "g"]:
        v.add_vocab(user_id=1, language="es", word=w, source="user", glossary="g")
    first = [i["word"] for i in v.review_next(user_id=1, language="es", n=50, shuffle=True)]
    second = [i["word"] for i in v.review_next(user_id=1, language="es", n=50, shuffle=True)]
    # Extremely unlikely to collide for 7 distinct items (1 / 5040 chance).
    assert first != second


def test_apply_grade_records_reviewed_at(fresh):
    """Grading must stamp reviewed_at so "reviewed today" is answerable."""
    from backend.services import vocab as v
    res = _seed_vocab(fresh)
    v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="easy")
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["reviewed_at"] is not None
    # reviewed_at uses datetime('now') so it should be near today (UTC).
    assert items[0]["reviewed_at"].startswith("20")


def test_list_vocab_filters_reviewed_range(fresh):
    """list_vocab(reviewed_after/reviewed_before) must bound by reviewed_at."""
    from backend.services import vocab as v
    a = _seed_vocab(fresh, word="a")
    b = _seed_vocab(fresh, word="b")
    # Grade a so it gets a reviewed_at stamp.
    v.apply_review_grade(user_id=1, vocab_id=a["id"], grade_value="easy")
    items = v.list_vocab(user_id=1, language="es")
    ra = items[0]["reviewed_at"]
    out = v.list_vocab(user_id=1, language="es",
                       reviewed_after="2000-01-01 00:00:00",
                       reviewed_before="2100-01-01 00:00:00")
    assert {i["word"] for i in out} == {"a"}
    out_far = v.list_vocab(user_id=1, language="es",
                           reviewed_after="2100-01-01 00:00:00",
                           reviewed_before="2200-01-01 00:00:00")
    assert out_far == []


def test_list_vocab_filters_added_range(fresh):
    """list_vocab(added_after/added_before) must bound by added_at."""
    from backend.services import vocab as v
    _seed_vocab(fresh, word="a")
    out = v.list_vocab(user_id=1, language="es",
                       added_after="2000-01-01 00:00:00",
                       added_before="2100-01-01 00:00:00")
    assert {i["word"] for i in out} == {"a"}
    out_far = v.list_vocab(user_id=1, language="es",
                           added_after="2100-01-01 00:00:00",
                           added_before="2200-01-01 00:00:00")
    assert out_far == []


# --- review_status edge cases ---------------------------------------------

def test_review_status_empty_language(fresh):
    from backend.services import vocab as v
    out = v.review_status(user_id=1, language="en")
    assert out["due"] == 0
    assert all(v == 0 for v in out["by_box"].values())
    assert sorted(out["by_box"].keys()) == [1, 2, 3, 4, 5]


def test_review_status_rejects_invalid_language(fresh):
    """review_status must validate the language argument, matching
    its siblings (review_next, list_vocab, count_vocab). The previous
    silent-pass behavior made `/api/vocab/review/status` return zeros
    instead of 400 for a typo like `?lang=eng`, which was inconsistent."""
    from backend.services import vocab as v
    with pytest.raises(ValueError, match="invalid language"):
        v.review_status(user_id=1, language="!!")


# --- apply_review_grade lookup error --------------------------------------

def test_apply_review_grade_unknown_vocab_raises(fresh):
    from backend.services import vocab as v
    with pytest.raises(LookupError):
        v.apply_review_grade(user_id=1, vocab_id=9999, grade_value="easy")


def test_apply_review_grade_returns_new_box_and_due(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    out = v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="easy")
    assert "vocab_id" in out
    assert "leitner_box" in out
    assert "next_due" in out


# --- find_vocab_box edge cases ---------------------------------------------

def test_find_vocab_box_empty_word_returns_none(fresh):
    from backend.services import vocab as v
    assert v.find_vocab_box(user_id=1, language="en", word="") is None
    assert v.find_vocab_box(user_id=1, language="en", word="   ") is None


def test_find_vocab_box_non_string_word_returns_none(fresh):
    from backend.services import vocab as v
    assert v.find_vocab_box(user_id=1, language="en", word=None) is None
    assert v.find_vocab_box(user_id=1, language="en", word=42) is None


def test_find_vocab_box_word_is_stripped_and_truncated(fresh):
    long_word = "a" * 300
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="en", word=long_word, source="user",
                      glossary="g")
    out = v.find_vocab_box(user_id=1, language="en", word=f"  {long_word}  ")
    assert out["id"] == res["id"]


def test_find_vocab_box_normalizes_spaces_to_underscore(fresh):
    """User-typed "snap at" must find a row stored as "snap_at" (the
    WordNet convention) and vice versa."""
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="en", word="snap_at",
                      source="user", glossary="g")
    out = v.find_vocab_box(user_id=1, language="en", word="snap at")
    assert out["id"] == res["id"]
    # And the reverse direction also works.
    res2 = v.add_vocab(user_id=1, language="en", word="look up",
                        source="user", glossary="g")
    out2 = v.find_vocab_box(user_id=1, language="en", word="look_up")
    assert out2["id"] == res2["id"]


# --- auto_add_from_lookup edge cases ---------------------------------------

def test_auto_add_skips_when_entry_has_no_senses(fresh):
    from backend.services.dictionaries.base import WordEntry
    from backend.services import vocab as v

    entry = WordEntry(word="x", language="en", senses=[], source="llm")
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is False


def test_auto_add_skips_when_sense_has_no_definitions(fresh):
    from backend.services.dictionaries.base import Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="x", language="en", source="llm",
        senses=[Sense(pos="noun", definitions=[])],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is False


def test_auto_add_logs_and_returns_false_on_validation_error(fresh):
    """An invalid language (somehow) must not crash auto-add."""
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="x", language="bad-lang!", source="llm",
        senses=[Sense(pos="noun", definitions=[Definition(glossary="g")])],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is False


def test_auto_add_defaults_source_to_llm_when_none(fresh):
    """If entry.source is missing, auto-add defaults it to 'llm'."""
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="auto", language="en", source="",
        senses=[Sense(pos="noun", definitions=[Definition(glossary="g")])],
    )
    v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    items = v.list_vocab(user_id=1, language="en")
    assert items[0]["source"] == "llm"


# --- HTTP endpoint validation ---------------------------------------------

def test_list_vocab_endpoint_missing_lang(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab")
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_lang"


def test_list_vocab_endpoint_invalid_lang(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab?lang=ENG123")
    assert r.status_code == 400


def test_add_vocab_endpoint_creates(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab", json={
        "language": "en", "word": "test", "source": "user",
        "glossary": "trial", "pos": "noun",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["created"] is True


def test_add_vocab_endpoint_invalid_input_returns_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab", json={"language": "en", "word": "x",
                                          "source": "user"})
    assert r.status_code == 400


def test_delete_vocab_endpoint_unknown_id_404(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.delete("/api/vocab/9999")
    assert r.status_code == 404


def test_delete_vocab_endpoint_happy_path(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.delete(f"/api/vocab/{res['id']}")
    assert r.status_code == 200
    assert "undo_token" in r.get_json()["data"]


def test_restore_vocab_endpoint_happy_path(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    del_r = client.delete(f"/api/vocab/{res['id']}")
    token = del_r.get_json()["data"]["undo_token"]
    r = client.post(f"/api/vocab/{res['id']}/restore", json={"undo_token": token})
    assert r.status_code == 200


def test_restore_vocab_endpoint_missing_token_400(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    client.delete(f"/api/vocab/{res['id']}")
    r = client.post(f"/api/vocab/{res['id']}/restore", json={})
    assert r.status_code == 400


def test_restore_vocab_endpoint_bad_token_type_400(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    client.delete(f"/api/vocab/{res['id']}")
    r = client.post(f"/api/vocab/{res['id']}/restore", json={"undo_token": 123})
    assert r.status_code == 400


def test_restore_vocab_endpoint_unknown_token_404(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post(f"/api/vocab/{res['id']}/restore",
                    json={"undo_token": "no-such-token"})
    assert r.status_code == 404


def test_patch_vocab_endpoint_box_must_be_int_not_bool(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    # Python bool is an int subclass; the validator rejects it explicitly.
    r = client.patch(f"/api/vocab/{res['id']}", json={"leitner_box": True})
    assert r.status_code == 400


def test_patch_vocab_endpoint_box_must_be_int_not_string(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.patch(f"/api/vocab/{res['id']}", json={"leitner_box": "3"})
    assert r.status_code == 400


def test_review_status_endpoint_happy(fresh):
    _seed_vocab(fresh, word="a")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/review/status?lang=es")
    assert r.status_code == 200
    assert r.get_json()["data"]["due"] >= 1


def test_review_status_endpoint_invalid_lang(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/review/status?lang=ENG")
    assert r.status_code == 400


def test_review_next_endpoint_happy(fresh):
    _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/review/next?lang=es&n=5")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["count"] >= 1
    assert isinstance(body["items"], list)


def test_review_next_endpoint_invalid_lang(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/review/next?lang=ENG")
    assert r.status_code == 400


def test_review_next_endpoint_box_filter(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    a = _seed_vocab(fresh, word="alpha", lang="en")
    _seed_vocab(fresh, word="beta", lang="en")
    from backend.services import vocab as v
    v.set_box(user_id=1, vocab_id=a["id"], box=4)
    r = client.get("/api/vocab/review/next?lang=en&n=10&box=4")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert {i["word"] for i in body["items"]} == {"alpha"}
    r_all = client.get("/api/vocab/review/next?lang=en&n=10&box=0")
    assert {i["word"] for i in r_all.get_json()["data"]["items"]} == {"alpha", "beta"}


def test_review_next_endpoint_box_invalid(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/review/next?lang=en&box=abc")
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_box"


def test_review_grade_endpoint_happy(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/review/grade", json={
        "vocab_id": res["id"], "grade": "easy",
    })
    assert r.status_code == 200
    assert r.get_json()["data"]["leitner_box"] == 2


def test_review_grade_endpoint_hard(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/review/grade", json={
        "vocab_id": res["id"], "grade": "hard",
    })
    assert r.status_code == 200
    assert r.get_json()["data"]["leitner_box"] == 1


def test_review_grade_endpoint_missing_vocab_id(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/review/grade", json={"grade": "easy"})
    assert r.status_code == 400


def test_review_grade_endpoint_missing_grade(fresh):
    res = _seed_vocab(fresh)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/review/grade", json={"vocab_id": res["id"]})
    assert r.status_code == 400


def test_review_grade_endpoint_unknown_vocab_404(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/review/grade", json={
        "vocab_id": 9999, "grade": "easy",
    })
    assert r.status_code == 404


def test_review_grade_endpoint_non_int_vocab_id(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/review/grade", json={
        "vocab_id": "not-an-int", "grade": "easy",
    })
    assert r.status_code == 400


def test_add_from_entry_returns_box_in_response(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "kiwi", "source": "user",
        "glossary": "small green fruit",
    })
    assert r.status_code == 200
    assert r.get_json()["data"]["leitner_box"] == 1


def test_add_from_entry_preserves_box_on_refresh(fresh):
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="en", word="kiwi", source="user",
                      glossary="old")
    v.set_box(user_id=1, vocab_id=res["id"], box=2)
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "kiwi", "source": "user",
        "glossary": "new",
    })
    assert r.status_code == 200
    assert r.get_json()["data"]["leitner_box"] == 2


def test_add_from_entry_rejects_bad_lang(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "ENG123", "word": "kiwi", "source": "user",
        "glossary": "g",
    })
    assert r.status_code == 400


def test_add_from_entry_rejects_empty_word(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en", "word": "  ", "source": "user",
        "glossary": "g",
    })
    assert r.status_code == 400


def test_lookup_endpoint_returns_hit_with_leading_trailing_space(fresh):
    """find_vocab_box strips the query; trailing/leading whitespace is
    tolerated."""
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="en", word="snap", source="user",
                      glossary="g")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/vocab/lookup?lang=en&word=%20snap%20")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["in_vocab"] is True
    assert body["vocab_id"] == res["id"]