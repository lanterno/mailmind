"""A person's preferences: the block in mailmind.config.json, read and written through the Directory, and their route. The file is
the owner's real one in use, so what a block's absence means and what a write leaves alone are held here."""
import json

import pytest

import problems
from mailmind.accounts.domain import preferences
from mailmind.bootstrap import accounts, assistant
from mailmind.kernel import send_window
from mailmind.kernel.errors import Refused

KEPT = {"anthropic_api_key": {"plain": "a-key"}, "signature_default": "Yours,\nAda", "webmail": ["https://mail.example.test"],
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


def test_the_defaults_are_what_the_app_does_with_no_block():
    assert preferences.DEFAULTS["undo_send_seconds"] == send_window.DEFAULT == 10
    assert preferences.DEFAULTS["density"] == "comfortable" and preferences.DEFAULTS["high_contrast"] is False
    assert preferences.DEFAULTS["single_keys"] is True and preferences.DEFAULTS["conversations"] is True


@pytest.mark.parametrize("block", [None, [], "dark", 7, {}])
def test_a_block_that_is_no_object_is_the_same_as_none(block):
    assert preferences.read({"preferences": block}) == dict(preferences.DEFAULTS)


@pytest.mark.parametrize("name, junk", [
    ("density", "tiny"), ("density", 3), ("theme", ["day"]), ("high_contrast", "yes"), ("high_contrast", 1),
    ("undo_send_seconds", 0), ("undo_send_seconds", 3600), ("undo_send_seconds", "10"), ("undo_send_seconds", True), ("undo_send_seconds", 10.5),
    ("quiet_from", "25:00"), ("quiet_from", "7:00"), ("quiet_until", None), ("draft_tone", "rude"), ("single_keys", None),
])
def test_a_value_edited_by_hand_that_cant_be_that_preference_is_its_default(name, junk):
    got = preferences.read({"preferences": {name: junk, "density": "compact"}} if name != "density" else {"preferences": {name: junk}})
    assert got[name] == preferences.DEFAULTS[name]
    if name != "density":
        assert got["density"] == "compact"


# ── writing ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_change_leaves_every_other_key_in_the_file_as_it_was(app, world):
    was = with_file(world)
    r = app.post("/api/preferences", {"density": "compact"})
    assert r.status == 200 and r.json["preferences"]["density"] == "compact"
    now = file_of(world)
    assert now.pop("preferences") == {"density": "compact"}
    assert now == was
    assert list(now) == list(was)                # in the order it had
    assert path_of(world).stat().st_mode & 0o077 == 0


def test_a_change_keeps_the_rest_of_the_block_and_what_it_doesnt_know(app, world):
    with_file(world, preferences={"theme": "dusk", "a_later_one": {"x": 1}})
    app.post("/api/preferences", {"high_contrast": True})
    assert file_of(world)["preferences"] == {"theme": "dusk", "a_later_one": {"x": 1}, "high_contrast": True}
    assert app.get("/api/preferences").json["preferences"]["theme"] == "dusk"


def test_a_value_none_can_be_refuses_the_whole_change_and_writes_nothing(app, world):
    with_file(world)
    before = path_of(world).read_bytes()
    r = app.post("/api/preferences", {"density": "compact", "undo_send_seconds": 2})
    assert (r.status, r.json) == (400, problems.refused("the Undo Send window must be 5 to 60 seconds"))
    assert path_of(world).read_bytes() == before


@pytest.mark.parametrize("change", [{"undo_send_seconds": send_window.SHORTEST}, {"undo_send_seconds": send_window.LONGEST}])
def test_each_end_of_the_undo_send_window_can_be_set(app, world, change):
    assert app.post("/api/preferences", change).json["preferences"]["undo_send_seconds"] == change["undo_send_seconds"]


@pytest.mark.parametrize("seconds", [send_window.SHORTEST - 1, send_window.LONGEST + 1, 0, 86400])
def test_past_either_end_of_the_window_is_refused(app, world, seconds):
    assert app.post("/api/preferences", {"undo_send_seconds": seconds}).status == 400
    assert "preferences" not in file_of(world)


@pytest.mark.parametrize("bad", ["25:00", "7:00", "22:60", "22:00:00", "soon"])
def test_a_quiet_hour_that_is_no_time_is_refused(app, world, bad):
    assert app.post("/api/preferences", {"quiet_from": bad}).status == 400


@pytest.mark.parametrize("field, bad", [("density", "tiny"), ("theme", "noon"), ("read_size", "xxl"), ("search_scope", "none"),
                                        ("high_contrast", "true"), ("undo_send_seconds", "soon")])
def test_what_the_schema_refuses_is_the_bad_request(app, world, field, bad):
    assert (lambda r: (r.status, r.json))(app.post("/api/preferences", {field: bad})) == (400, problems.BAD_REQUEST)
    assert "preferences" not in file_of(world)


def test_a_file_that_wont_parse_is_never_overwritten(app, world):
    path_of(world).write_text("{ not json")
    with pytest.raises(ValueError):
        accounts.config.set_preferences({"density": "cozy"})
    assert path_of(world).read_text() == "{ not json"


def test_a_change_of_nothing_writes_nothing(app, world):
    before = path_of(world).stat().st_mtime_ns
    assert app.post("/api/preferences", {}).status == 200
    assert path_of(world).stat().st_mtime_ns == before


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


def test_what_cant_be_a_preference_is_left_out_without_a_word(app, world):
    r = app.post("/api/preferences/import", {"held": {"theme": "noon", "made_up": 1, "single_keys": "1", "ai_daily_limit_usd": 2, "conversations": False}})
    assert r.status == 200
    assert file_of(world)["preferences"] == {"conversations": False}
    assert "ai_daily_limit_usd" not in file_of(world)


def test_with_nothing_to_bring_in_the_file_is_not_written(app, world):
    with_file(world, preferences={"density": "cozy"})
    before = path_of(world).read_bytes()
    r = app.post("/api/preferences/import", {"held": {"density": "compact", "theme": "noon"}})
    assert r.status == 200 and r.json["preferences"]["density"] == "cozy"
    assert path_of(world).read_bytes() == before


# ── the Directory, as the routes use it ──────────────────────────────────────────────────────────────────────────────


def test_one_preference_is_what_the_file_says_or_none(app, world):
    assert accounts.config.preference("undo_send_seconds") is None
    with_file(world, preferences={"undo_send_seconds": 30})
    assert accounts.config.preference("undo_send_seconds") == 30


def test_changes_are_judged_whole_before_anything_is_read():
    with pytest.raises(Refused):
        preferences.changes({"density": "compact", "theme": "noon"})
    assert preferences.changes({"density": "compact", "made_up": 1}) == {"density": "compact"}


def test_the_first_run_guide_is_offered_once_and_the_file_remembers(app, world):
    """It lives on the server so that clearing a browser's data doesn't offer it again."""
    assert app.get("/api/preferences").json["preferences"]["guide_offered"] is False
    assert app.post("/api/preferences", {"guide_offered": True}).json["preferences"]["guide_offered"] is True
    assert file_of(world)["preferences"]["guide_offered"] is True
    assert app.get("/api/preferences").json["preferences"]["guide_offered"] is True


@pytest.mark.parametrize("value", ["yes", 1, "true"])
def test_the_first_run_guide_is_a_yes_or_no(app, value):
    assert app.post("/api/preferences", {"guide_offered": value}).status == 400
