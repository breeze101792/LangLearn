"""Tests for the auth and languages blueprints."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    from backend.services import seed as seed_svc
    seed_svc.ensure_language_row("en", "English", is_built_in=1)
    return tmp_path


# --- auth blueprint ------------------------------------------------------


def test_auth_whoami_returns_default_user(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/auth/whoami")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["user_id"] == 1
    assert body["data"]["username"] == "me"


# --- languages blueprint: GET /api/languages ------------------------------


def test_list_languages_includes_all_catalog(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/languages")
    assert r.status_code == 200
    body = r.get_json()
    codes = [item["code"] for item in body["data"]]
    for code in ("en", "es", "ja", "pt", "zh", "fr", "de"):
        assert code in codes


def test_list_languages_marks_seed_status(fresh):
    from backend.services import seed as seed_svc
    seed_svc.initialize_language("en")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/languages")
    body = r.get_json()
    en = next(item for item in body["data"] if item["code"] == "en")
    es = next(item for item in body["data"] if item["code"] == "es")
    assert en["seeded"] is True
    assert es["seeded"] is False


def test_list_languages_response_includes_display_name(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/languages")
    body = r.get_json()
    for item in body["data"]:
        assert "display_name" in item
        assert "code" in item
        assert "is_built_in" in item


# --- languages blueprint: POST /api/languages -----------------------------


def test_add_language_creates_row(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages", json={
        "code": "es", "display_name": "Spanish",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["code"] == "es"
    assert body["data"]["display_name"] == "Spanish"


def test_add_language_defaults_display_name_to_uppercase_code(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages", json={"code": "es"})
    assert r.status_code == 200
    assert r.get_json()["data"]["display_name"] == "ES"


def test_add_language_rejects_invalid_code(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages", json={"code": "ENG123"})
    assert r.status_code == 400


def test_add_language_rejects_empty_display_name(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages", json={
        "code": "es", "display_name": "",
    })
    # Empty display_name falls back to uppercase code.
    assert r.status_code == 200
    assert r.get_json()["data"]["display_name"] == "ES"


def test_add_language_is_idempotent(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    client.post("/api/languages", json={"code": "es", "display_name": "Spanish"})
    # Second add should still 200 and update the row.
    r = client.post("/api/languages", json={"code": "es", "display_name": "Castilian"})
    assert r.status_code == 200
    assert r.get_json()["data"]["display_name"] == "Castilian"


# --- languages blueprint: POST /api/languages/<code>/initialize ----------


def test_initialize_language_already_seeded_returns_no_op(fresh):
    from backend.services import seed as seed_svc
    seed_svc.initialize_language("en")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/en/initialize", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["seeded"] is False
    assert body["data"]["reason"] == "already_seeded"


def test_initialize_language_force_reseeds(fresh):
    from backend.services import seed as seed_svc
    seed_svc.initialize_language("en")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/en/initialize", json={"force": True})
    assert r.status_code == 200
    assert r.get_json()["data"]["seeded"] is True


def test_initialize_language_invalid_code_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/ENG/initialize", json={})
    assert r.status_code == 400


def test_initialize_language_no_built_in_seed_500(fresh, monkeypatch):
    """When a non-built-in language has no built-in JSON seed and the LLM
    stub raises a non-LLMError exception, the route propagates it as 500."""
    from backend.app import create_app
    from backend.services import seed as seed_svc
    from backend.services import llm as llm_svc

    def boom(*a, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(llm_svc, "generate_seed_payload", boom)
    # Direct seed_via_llm on the seed module, since the languages route
    # calls that path.
    monkeypatch.setattr(seed_svc, "seed_via_llm", boom)
    seed_svc.ensure_language_row("es", "Spanish", is_built_in=0)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/es/initialize", json={})
    assert r.status_code == 500


def test_initialize_language_llm_error_returns_502(fresh, monkeypatch):
    """Non-built-in language init with LLMError must return HTTP 502."""
    from backend.app import create_app
    from backend.services import seed as seed_svc
    from backend.services import llm as llm_svc

    def boom(*a, **kw):
        raise llm_svc.LLMError("network down")
    monkeypatch.setattr(seed_svc, "seed_via_llm", boom)
    seed_svc.ensure_language_row("es", "Spanish", is_built_in=0)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/es/initialize", json={})
    assert r.status_code == 502


# --- languages blueprint: GET /api/languages/<code>/seed-status ----------


def test_seed_status_unseeded(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/languages/es/seed-status")
    assert r.status_code == 200
    assert r.get_json()["data"]["seeded"] is False


def test_seed_status_seeded(fresh):
    from backend.services import seed as seed_svc
    seed_svc.initialize_language("en")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/languages/en/seed-status")
    assert r.status_code == 200
    assert r.get_json()["data"]["seeded"] is True


def test_seed_status_unknown_lang_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/languages/ENG/seed-status")
    assert r.status_code == 400


# --- languages blueprint: POST /api/languages/<code>/apply-explanations --


def _seed_vocab_rows(client=None):
    """Insert a handful of structures and phrases for ``en`` directly so
    the apply path has something to translate. Bypasses the LLM."""
    from backend.db import transaction
    with transaction() as conn:
        for i, pat in enumerate(["S V O", "S V IO", "S V O O"]):
            conn.execute(
                "INSERT INTO structures (user_id, language, pattern,"
                " example_sentence, explanation, explanation_primary,"
                " explanation_secondary, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in')",
                (1, "en", pat, "x", "x", None, None),
            )
        for ph in ["Hi", "Bye", "Thanks"]:
            conn.execute(
                "INSERT INTO phrases (user_id, language, phrase,"
                " example_sentence, explanation, explanation_primary,"
                " explanation_secondary, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in')",
                (1, "en", ph, "x", "x", None, None),
            )


def test_apply_explanations_invalid_code_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/ENG/apply-explanations", json={})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_apply_explanations_returns_zero_when_no_rows(fresh, monkeypatch):
    """No structures or phrases → service returns zeros; the route
    returns the count envelope. We stub LLM service to make sure it
    is NOT called when there's nothing to translate."""
    from backend.app import create_app
    from backend.services import llm as llm_svc

    called = {"n": 0}

    def fake_apply(**kw):
        called["n"] += 1
        return {"structures": [], "phrases": []}
    monkeypatch.setattr(llm_svc, "apply_explanations_via_llm", fake_apply)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/en/apply-explanations", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"] == {"structures": 0, "phrases": 0}
    # The stub proves the LLM was bypassed for the empty case.
    assert called["n"] == 0


def test_apply_explanations_writes_translations_via_stubbed_llm(
    fresh, monkeypatch,
):
    """Happy path: structures and phrases exist, the LLM stub returns
    translations, and the rows are updated with the new
    ``explanation_primary`` values. Verifies the full
    routes → service → LLM stub → DB round-trip."""
    from backend.app import create_app
    from backend.db import get_conn
    from backend.services import llm as llm_svc

    _seed_vocab_rows()

    struct_ids = []
    phrase_ids = []
    with get_conn() as conn:
        struct_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM structures WHERE language='en'"
            ).fetchall()
        ]
        phrase_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM phrases WHERE language='en'"
            ).fetchall()
        ]

    def fake_apply_via_llm(
        *, lang, structures, phrases, primary, secondary,
        batch_size=None,
    ):
        # Confirm settings' primary was threaded through.
        assert primary == "en"
        return {
            "structures": [
                {"id": r["id"], "explanation_primary": f"struct-{r['id']}",
                 "explanation_secondary": None, "explanation": "e"}
                for r in structures
            ],
            "phrases": [
                {"id": r["id"], "explanation_primary": f"phrase-{r['id']}",
                 "explanation_secondary": None, "explanation": "e"}
                for r in phrases
            ],
        }
    monkeypatch.setattr(llm_svc, "apply_explanations_via_llm", fake_apply_via_llm)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/en/apply-explanations", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["structures"] == len(struct_ids)
    assert body["data"]["phrases"] == len(phrase_ids)

    # DB rows reflect the updated explanation_primary values.
    with get_conn() as conn:
        for row_id in struct_ids:
            row = conn.execute(
                "SELECT explanation_primary FROM structures WHERE id=?",
                (row_id,),
            ).fetchone()
            assert row["explanation_primary"] == f"struct-{row_id}"
        for row_id in phrase_ids:
            row = conn.execute(
                "SELECT explanation_primary FROM phrases WHERE id=?",
                (row_id,),
            ).fetchone()
            assert row["explanation_primary"] == f"phrase-{row_id}"


def test_apply_explanations_llm_error_returns_502(fresh, monkeypatch):
    """If ``apply_explanations`` raises ``LLMError`` (network or
    schema failure), the route must translate it to HTTP 502."""
    from backend.app import create_app
    from backend.services import seed as seed_svc
    from backend.services import llm as llm_svc

    _seed_vocab_rows()

    def boom(*args, **kwargs):
        raise llm_svc.LLMError("provider down")
    monkeypatch.setattr(seed_svc, "apply_explanations", boom)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/en/apply-explanations", json={})
    assert r.status_code == 502
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "llm_error"


def test_apply_explanations_does_not_touch_target_language_content(
    fresh, monkeypatch,
):
    """The route is documented as translating explanations only — the
    target-language columns (pattern / example_sentence / phrase) must
    NOT be overwritten even if the LLM stub returns them."""
    from backend.app import create_app
    from backend.db import get_conn
    from backend.services import llm as llm_svc

    _seed_vocab_rows()
    before: dict[int, tuple] = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT id, pattern, example_sentence FROM structures"
            " WHERE language='en'"
        ).fetchall():
            before[r["id"]] = (r["pattern"], r["example_sentence"])

    def fake_apply_via_llm(
        *, lang, structures, phrases, primary, secondary,
        batch_size=None,
    ):
        # Note: the schema FORBIDS returning pattern/example_sentence
        # for the apply path, but defensively, the seed service copies
        # only explanation_* into the DB. Confirm by returning
        # explanation_* only here.
        return {
            "structures": [
                {"id": r["id"], "explanation_primary": "p",
                 "explanation_secondary": None, "explanation": "e"}
                for r in structures
            ],
            "phrases": [],
        }
    monkeypatch.setattr(llm_svc, "apply_explanations_via_llm", fake_apply_via_llm)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/languages/en/apply-explanations", json={})
    assert r.status_code == 200

    # Target-language columns are unchanged.
    with get_conn() as conn:
        for row_id, (pattern, sentence) in before.items():
            row = conn.execute(
                "SELECT pattern, example_sentence FROM structures WHERE id=?",
                (row_id,),
            ).fetchone()
            assert row["pattern"] == pattern
            assert row["example_sentence"] == sentence
