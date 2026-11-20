"""A person's preferences: the block in mailmind.config.json, read and written through the Directory, and their route. The file is
the owner's real one in use, so what a block's absence means and what a write leaves alone are held here."""
import json

import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain import preferences
from mailmind.kernel import send_window
from mailmind.kernel.errors import Refused

ADA = {"email": "ada@example.test", "type": "imap", "host": "imap.example.test", "credential": {"keychain": "account:ada@example.test", "version": 1}}
KEPT = {"anthropic_api_key": {"keychain": "anthropic-api-key", "version": 1}, "signature_default": "Yours,\nAda", "webmail": ["https://mail.example.test"],
        "something_a_later_version_wrote": {"deep": [1, 2.5, "ü"]}}


def test_the_defaults_are_what_the_app_does_with_no_block():
    assert preferences.DEFAULTS["undo_send_seconds"] == send_window.DEFAULT == 10
    assert preferences.DEFAULTS["density"] == "comfortable" and preferences.DEFAULTS["high_contrast"] is False
    assert preferences.DEFAULTS["single_keys"] is True and preferences.DEFAULTS["conversations"] is True


def test_a_fresh_install_follows_the_system_and_the_clock_and_a_theme_the_file_holds_stands():
    assert preferences.read({})["theme"] == "auto"
    assert preferences.read({"preferences": {"theme": "day"}})["theme"] == "day"
    assert preferences.changes({"theme": "auto"}) == {"theme": "auto"}


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


def test_changes_are_judged_whole_before_anything_is_read():
    with pytest.raises(Refused):
        preferences.changes({"density": "compact", "theme": "noon"})
    assert preferences.changes({"density": "compact", "made_up": 1}) == {"density": "compact"}


# ── through the Directory, in the file ───────────────────────────────────────────────────────────────────────────────

def path_of(data_dir):
    return data_dir / "mailmind.config.json"


def file_of(data_dir):
    return json.loads(path_of(data_dir).read_text())


def with_file(data_dir, **more):
    path_of(data_dir).write_text(json.dumps({**file_of(data_dir), **more}))


@pytest.fixture
def config(data_dir):
    """The Directory over a file holding an account, and keys of its own beside it as the owner's may."""
    path_of(data_dir).write_text(json.dumps({"accounts": [ADA], **KEPT}))
    return Directory(config_file.ConfigFile(), secrets, lambda: None)


def test_a_change_leaves_every_other_key_in_the_file_as_it_was(config, data_dir):
    was = file_of(data_dir)
    assert config.set_preferences({"density": "compact"})["density"] == "compact"
    now = file_of(data_dir)
    assert now.pop("preferences") == {"density": "compact"}
    assert now == was
    assert list(now) == list(was)                # in the order it had
    assert path_of(data_dir).stat().st_mode & 0o077 == 0


def test_a_change_keeps_the_rest_of_the_block_and_what_it_doesnt_know(config, data_dir):
    with_file(data_dir, preferences={"theme": "dusk", "a_later_one": {"x": 1}})
    config.set_preferences({"high_contrast": True})
    assert file_of(data_dir)["preferences"] == {"theme": "dusk", "a_later_one": {"x": 1}, "high_contrast": True}
    assert config.preferences()["theme"] == "dusk"


@pytest.mark.parametrize("seconds", [send_window.SHORTEST, send_window.LONGEST])
def test_each_end_of_the_undo_send_window_can_be_set(config, seconds):
    assert config.set_preferences({"undo_send_seconds": seconds})["undo_send_seconds"] == seconds


@pytest.mark.parametrize("change, said", [
    *(({"undo_send_seconds": s}, r"The Undo Send window must be 5 to 60 seconds\.$")
      for s in (send_window.SHORTEST - 1, send_window.LONGEST + 1, 0, 86400)),
    *(({"quiet_from": t}, r"Quiet hours need a time such as 22:00\.$") for t in ("25:00", "7:00", "22:60", "22:00:00", "soon")),
    ({"quiet_until": "soon"}, r"Quiet hours need a time such as 07:00\.$"),
    ({"density": "huge"}, r"That can’t be the density setting\.$"),
], ids=repr)
def test_a_value_no_preference_can_be_is_refused_and_nothing_is_written(config, data_dir, change, said):
    before = path_of(data_dir).read_bytes()
    with pytest.raises(Refused, match=f"^{said}"):
        config.set_preferences(change)
    assert path_of(data_dir).read_bytes() == before


def test_a_file_that_wont_parse_is_never_overwritten(config, data_dir):
    path_of(data_dir).write_text("{ not json")
    with pytest.raises(ValueError):
        config.set_preferences({"density": "cozy"})
    assert path_of(data_dir).read_text() == "{ not json"


@pytest.mark.parametrize("change", [{}, {"made_up": 1}])
def test_a_change_of_nothing_writes_nothing(config, data_dir, change):
    before = path_of(data_dir).stat().st_mtime_ns, path_of(data_dir).read_bytes()
    assert config.set_preferences(change) == {**preferences.DEFAULTS, "ai_enabled": True}     # the file names Claude's key
    assert (path_of(data_dir).stat().st_mtime_ns, path_of(data_dir).read_bytes()) == before


def test_what_cant_be_a_preference_is_left_out_of_what_a_browser_held_without_a_word(config, data_dir):
    held = {"theme": "noon", "made_up": 1, "single_keys": "1", "ai_daily_limit_usd": 2, "conversations": False}
    assert config.import_preferences(held)["conversations"] is False
    assert file_of(data_dir)["preferences"] == {"conversations": False}
    assert "ai_daily_limit_usd" not in file_of(data_dir)


def test_with_nothing_to_bring_in_the_file_is_not_written(config, data_dir):
    with_file(data_dir, preferences={"density": "cozy"})
    before = path_of(data_dir).read_bytes()
    assert config.import_preferences({"density": "compact", "theme": "noon"})["density"] == "cozy"
    assert path_of(data_dir).read_bytes() == before


def test_one_preference_is_what_the_file_says_or_none(config, data_dir):
    assert config.preference("undo_send_seconds") is None
    with_file(data_dir, preferences={"undo_send_seconds": 30})
    assert config.preference("undo_send_seconds") == 30
    with_file(data_dir, preferences={"undo_send_seconds": 3600})
    assert config.preference("undo_send_seconds") is None


def test_the_first_run_guide_is_offered_once_and_the_file_remembers(config, data_dir):
    """It lives on the server so that clearing a browser's data doesn't offer it again."""
    assert config.preferences()["guide_offered"] is False
    assert config.set_preferences({"guide_offered": True})["guide_offered"] is True
    assert file_of(data_dir)["preferences"]["guide_offered"] is True
    assert Directory(config_file.ConfigFile(), secrets, lambda: None).preferences()["guide_offered"] is True


# ── AI: on or off, its provider, model and service URL (#49) ──────────────────────────────────────────────────────────

def test_ai_mode_starts_off_with_claude_and_its_own_model_and_url():
    assert {k: v for k, v in preferences.DEFAULTS.items() if k.startswith("ai_")} == {
        "ai_enabled": False, "ai_provider": "anthropic", "ai_model": "", "ai_base_url": ""}
    assert preferences.CHOICES["ai_provider"] == ("anthropic", "openai", "gemini", "local", "compatible")


def directory_over(data_dir, environment_key=False, **top):
    path_of(data_dir).write_text(json.dumps({"accounts": [ADA], **top}))
    return Directory(config_file.ConfigFile(), secrets, lambda provider: None, lambda: environment_key)


def test_ai_mode_the_file_doesnt_say_is_off_with_no_key_anywhere(data_dir):
    assert directory_over(data_dir).preferences()["ai_enabled"] is False
    assert directory_over(data_dir, preferences={"ai_enabled": "yes"}).preferences()["ai_enabled"] is False


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "local", "compatible"])
def test_ai_mode_the_file_doesnt_say_is_on_once_it_names_a_key_for_any_provider(data_dir, provider):
    named = {f"{provider}_api_key": {"keychain": f"{provider}-api-key", "version": 1}}
    assert directory_over(data_dir, **named).preferences()["ai_enabled"] is True


def test_ai_mode_the_file_doesnt_say_is_on_with_claudes_key_in_the_environment(data_dir):
    assert directory_over(data_dir, environment_key=True).preferences()["ai_enabled"] is True
    assert directory_over(data_dir, mistral_api_key={"env": "NOT_A_PROVIDER"}).preferences()["ai_enabled"] is False


def test_ai_mode_the_file_says_wins_over_any_key(data_dir):
    off = directory_over(data_dir, environment_key=True, anthropic_api_key={"env": "K"}, preferences={"ai_enabled": False})
    assert off.preferences()["ai_enabled"] is False
    assert directory_over(data_dir, preferences={"ai_enabled": True}).preferences()["ai_enabled"] is True


def test_ai_mode_set_is_written_and_its_answer_is_what_stands(data_dir):
    d = directory_over(data_dir, environment_key=True)
    assert d.set_preferences({"density": "cozy"})["ai_enabled"] is True
    assert "ai_enabled" not in file_of(data_dir)["preferences"]          # the default isn't written for it
    assert d.set_preferences({"ai_enabled": False})["ai_enabled"] is False
    assert file_of(data_dir)["preferences"]["ai_enabled"] is False


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "llama3.3:70b-instruct-q4_K_M", "openai/gpt-6.1-sol", "models/gemini-4-hydrogen",
                                   "me@example.test/finetune+v2", "a" * 200, ""])
def test_a_model_is_up_to_two_hundred_of_its_characters_or_none(config, model):
    assert config.set_preferences({"ai_model": model})["ai_model"] == model


@pytest.mark.parametrize("model", ["a" * 201, "gpt 6", "gpt-6\n", "ünï", "gpt;6", "gpt-6?", 7, None, ["gpt-6"]])
def test_a_model_of_anything_else_is_refused_in_words(config, data_dir, model):
    before = path_of(data_dir).read_bytes()
    with pytest.raises(Refused, match=r"^The AI model’s name must be up to 200 letters, digits and \. _ : / @ \+ - characters\.$"):
        config.set_preferences({"ai_model": model})
    assert path_of(data_dir).read_bytes() == before


@pytest.mark.parametrize("url, kept", [
    ("https://llm.example.test/v1", "https://llm.example.test/v1"),
    ("https://llm.example.test/v1/", "https://llm.example.test/v1"),
    ("https://llm.example.test:8443/openai/v1", "https://llm.example.test:8443/openai/v1"),
    ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
    ("http://localhost:8080/v1/", "http://localhost:8080/v1"),
    ("http://[::1]:11434/v1", "http://[::1]:11434/v1"),
    ("http://[::1]/v1", "http://[::1]/v1"),
    ("https://[fe80::1]/v1", "https://[fe80::1]/v1"),
    ("https://llm.example.test", "https://llm.example.test"),
    ("https://llm.example.test:1/v1", "https://llm.example.test:1/v1"),
    ("https://llm.example.test:65535/v1", "https://llm.example.test:65535/v1"),
    ("", ""),
])
def test_a_service_url_is_https_or_http_on_this_mac_kept_without_its_trailing_slash(config, data_dir, url, kept):
    assert config.set_preferences({"ai_base_url": url})["ai_base_url"] == kept
    assert file_of(data_dir)["preferences"]["ai_base_url"] == kept


@pytest.mark.parametrize("url", [
    "http://llm.example.test/v1",                     # http that leaves this Mac
    "http://10.0.0.7:11434/v1",
    "ftp://llm.example.test/v1",
    "llm.example.test/v1",
    "https:///v1",
    "https://user:pw@llm.example.test/v1",
    "https://user@llm.example.test/v1",
    "https://llm.example.test/v1?key=x",
    "https://llm.example.test/v1?",
    "https://llm.example.test/v1#top",
    "https://llm.example.test:99999/v1",
    "https://llm.example.test:65536/v1",
    "https://llm.example.test:port/v1",
    "https://llm.example.test:/v1",                   # a port left empty
    "http://127.0.0.1:/v1",
    "http://[::1]:/v1",
    "https://llm.example.test:0/v1",                  # a port none listens on
    "http://localhost:00/v1",
    "https://[v1.ab]/v1",                             # IPvFuture, which no client here reaches
    "https://[V1.ab]/v1",
    "https://[fe80::1%25en0]/v1",                     # an IPv6 address with a zone
    "http://[::1%25lo0]:11434/v1",
    "https://[1.2.3.4]/v1",
    "https://llm.example.test/v 1",
    "https://llm.example.test/ü",
    "https://llm.example.test/" + "a" * 476,          # 501 characters
    7, None,
])
def test_a_service_url_of_anything_else_is_refused_in_words(config, data_dir, url):
    before = path_of(data_dir).read_bytes()
    with pytest.raises(Refused, match="^The AI service’s URL must be https, or http on this Mac, in at most 500 characters, "
                                      r"with no user, password, query or fragment\.$"):
        config.set_preferences({"ai_base_url": url})
    assert path_of(data_dir).read_bytes() == before


def test_the_longest_url_is_five_hundred_characters():
    longest = "https://llm.example.test/" + "a" * 475
    assert len(longest) == preferences.AI_BASE_URL_MAX and preferences.changes({"ai_base_url": longest})["ai_base_url"] == longest


@pytest.mark.parametrize("change", [{"ai_provider": "mistral"}, {"ai_enabled": "yes"}, {"ai_enabled": 1}])
def test_a_provider_or_switch_none_can_be_is_refused(config, change):
    with pytest.raises(Refused):
        config.set_preferences(change)


def test_choosing_another_provider_sets_its_model_and_url_back_to_its_own(config, data_dir):
    config.set_preferences({"ai_provider": "compatible", "ai_model": "mixtral-9x", "ai_base_url": "https://llm.example.test/v1"})
    now = config.set_preferences({"ai_provider": "openai"})
    assert (now["ai_provider"], now["ai_model"], now["ai_base_url"]) == ("openai", "", "")
    assert file_of(data_dir)["preferences"] == {"ai_provider": "openai", "ai_model": "", "ai_base_url": ""}


def test_a_change_of_provider_keeps_the_model_and_url_it_names_itself(config):
    config.set_preferences({"ai_provider": "compatible", "ai_model": "mixtral-9x", "ai_base_url": "https://llm.example.test/v1"})
    now = config.set_preferences({"ai_provider": "local", "ai_model": "llama3.3"})
    assert (now["ai_provider"], now["ai_model"], now["ai_base_url"]) == ("local", "llama3.3", "")
    now = config.set_preferences({"ai_provider": "compatible", "ai_base_url": "http://127.0.0.1:8080/v1"})
    assert (now["ai_model"], now["ai_base_url"]) == ("", "http://127.0.0.1:8080/v1")


def test_the_same_provider_again_keeps_its_model_and_url(config):
    config.set_preferences({"ai_provider": "local", "ai_model": "llama3.3", "ai_base_url": "http://127.0.0.1:11500/v1"})
    now = config.set_preferences({"ai_provider": "local", "ai_enabled": False})
    assert (now["ai_model"], now["ai_base_url"], now["ai_enabled"]) == ("llama3.3", "http://127.0.0.1:11500/v1", False)
    assert config.set_preferences({"ai_model": "qwen3"})["ai_base_url"] == "http://127.0.0.1:11500/v1"


@pytest.mark.parametrize("name, junk", [("ai_provider", "mistral"), ("ai_enabled", "no"), ("ai_model", "has space"),
                                        ("ai_base_url", "http://llm.example.test/v1"), ("ai_base_url", 7),
                                        ("ai_base_url", "https://[fe80::1%25en0]/v1"), ("ai_base_url", "https://[v1.ab]/v1")])
def test_an_ai_preference_edited_by_hand_that_cant_be_it_is_its_default(name, junk):
    assert preferences.read({"preferences": {name: junk}})[name] == preferences.DEFAULTS[name]


def test_a_url_edited_by_hand_with_a_trailing_slash_reads_without_it():
    assert preferences.read({"preferences": {"ai_base_url": "https://llm.example.test/v1/"}})["ai_base_url"] == \
        "https://llm.example.test/v1"


LOCAL_ELSEWHERE = "^A model on this Mac must be at 127.0.0.1, ::1 or localhost.$"


@pytest.mark.parametrize("url", ["http://127.0.0.1:1234/v1", "https://localhost:8443/v1", "http://[::1]:8080/v1", ""])
def test_a_model_on_this_mac_is_at_a_url_on_this_mac(config, url):
    now = config.set_preferences({"ai_provider": "local", "ai_base_url": url})
    assert (now["ai_provider"], now["ai_base_url"]) == ("local", url)


def test_a_model_on_this_mac_at_a_url_elsewhere_is_refused_whichever_the_change_names(config, data_dir):
    before = path_of(data_dir).read_bytes()
    with pytest.raises(Refused, match=LOCAL_ELSEWHERE):
        config.set_preferences({"ai_provider": "local", "ai_base_url": "https://gpu.example.test/v1"})
    assert path_of(data_dir).read_bytes() == before
    config.set_preferences({"ai_provider": "local"})
    with pytest.raises(Refused, match=LOCAL_ELSEWHERE):
        config.set_preferences({"ai_base_url": "https://gpu.example.test/v1"})
    config.set_preferences({"ai_provider": "compatible", "ai_base_url": "https://gpu.example.test/v1"})
    with pytest.raises(Refused, match=LOCAL_ELSEWHERE):
        config.set_preferences({"ai_provider": "local", "ai_base_url": "https://gpu.example.test/v1"})
    assert config.set_preferences({"ai_provider": "local"})["ai_base_url"] == ""        # the last provider's goes


def test_a_url_that_cant_be_any_services_is_refused_in_its_own_words_first(config):
    with pytest.raises(Refused, match="^The AI service’s URL must be"):
        config.set_preferences({"ai_provider": "local", "ai_base_url": "http://10.0.0.7:11434/v1"})


def test_a_model_on_this_mac_edited_by_hand_to_a_url_elsewhere_reads_at_its_own():
    raw = {"preferences": {"ai_provider": "local", "ai_base_url": "https://gpu.example.test/v1"}}
    assert preferences.read(raw)["ai_base_url"] == ""
    assert preferences.read({"preferences": {"ai_provider": "compatible", "ai_base_url": "https://gpu.example.test/v1"}})[
        "ai_base_url"] == "https://gpu.example.test/v1"
