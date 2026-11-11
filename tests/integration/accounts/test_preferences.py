"""A person's preferences through their routes: what the page reads with nothing set, a change refused whole, what the schemas
refuse, the AI's daily limit where the Assistant reads it, and what a browser held brought in. What the Directory keeps in the file,
and how, is tests/unit/accounts/test_preferences.py."""
import json

import pytest

import problems
from mailmind.accounts.domain import preferences
from mailmind.bootstrap import assistant

KEPT = {"anthropic_api_key": {"keychain": "anthropic-api-key", "version": 1}, "signature_default": "Yours,\nAda", "webmail": ["https://mail.example.test"],
        "something_a_later_version_wrote": {"deep": [1, 2.5, "ü"]}}


def path_of(world):
    return world.dir / "mailmind.config.json"


def file_of(world):
    return json.loads(path_of(world).read_text())


def with_file(world, **more):
    raw = {**file_of(world), **KEPT, **more}
    path_of(world).write_text(json.dumps(raw))
    return raw


# ── nothing said ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_file_with_no_block_answers_each_default_and_reading_writes_nothing(app, world):
    before = path_of(world).read_bytes()
    r = app.get("/api/preferences")
    assert r.status == 200 and r.json["ok"] is True
    assert r.json["preferences"] == {**preferences.DEFAULTS, "ai_daily_limit_usd": 15.0}
    assert path_of(world).read_bytes() == before


# ── writing ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_value_none_can_be_refuses_the_whole_change_and_writes_nothing(app, world):
    with_file(world)
    before = path_of(world).read_bytes()
    r = app.post("/api/preferences", {"density": "compact", "undo_send_seconds": 2})
    assert (r.status, r.json) == (400, problems.refused("the Undo Send window must be 5 to 60 seconds"))
    assert path_of(world).read_bytes() == before


@pytest.mark.parametrize("field, bad", [("density", "tiny"), ("theme", "noon"), ("read_size", "xxl"), ("search_scope", "none"),
                                        ("high_contrast", "true"), ("undo_send_seconds", "soon")])
def test_what_the_schema_refuses_is_the_bad_request(app, world, field, bad):
    assert (lambda r: (r.status, r.json))(app.post("/api/preferences", {field: bad})) == (400, problems.BAD_REQUEST)
    assert "preferences" not in file_of(world)


# ── the AI's daily limit ─────────────────────────────────────────────────────────────────────────────────────────────


def test_the_ai_limit_goes_at_the_top_level_where_the_governor_reads_it(app, world):
    r = app.post("/api/preferences", {"ai_daily_limit_usd": 2.5})
    assert r.status == 200 and r.json["preferences"]["ai_daily_limit_usd"] == 2.5
    raw = file_of(world)
    assert raw["ai_daily_limit_usd"] == 2.5 and "preferences" not in raw
    assert assistant.limit_micros() == 2_500_000


def test_a_limit_the_file_already_held_is_what_the_page_is_shown(app, world):
    with_file(world, ai_daily_limit_usd=3)
    assert app.get("/api/preferences").json["preferences"]["ai_daily_limit_usd"] == 3.0


@pytest.mark.parametrize("bad, code", [(0, 400), (-1, 400), (1001, 400), (True, 400), ("12", 400)])
def test_a_limit_the_reader_wouldnt_take_or_a_slip_is_refused_and_not_stored(app, world, bad, code):
    assert app.post("/api/preferences", {"ai_daily_limit_usd": bad}).status == code
    assert "ai_daily_limit_usd" not in file_of(world)


# ── what a browser held ──────────────────────────────────────────────────────────────────────────────────────────────


def test_what_a_browser_held_comes_in_only_for_what_the_file_doesnt_say(app, world):
    with_file(world, preferences={"theme": "dusk"})
    r = app.post("/api/preferences/import", {"held": {"theme": "night", "density": "cozy", "read_size": "xl"}})
    assert r.status == 200
    assert file_of(world)["preferences"] == {"theme": "dusk", "density": "cozy", "read_size": "xl"}
    assert (r.json["preferences"]["theme"], r.json["preferences"]["read_size"]) == ("dusk", "xl")


@pytest.mark.parametrize("value", ["yes", 1, "true"])
def test_the_first_run_guide_is_a_yes_or_no(app, value):
    assert app.post("/api/preferences", {"guide_offered": value}).status == 400
