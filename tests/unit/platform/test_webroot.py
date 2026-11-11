"""The page and its files, as the build holds them. Every test has a tiny build (conftest, tests/fixtures/tiny_build.py), and
one that wants nothing built, or to change a file, makes its own."""
import os

import pytest

from fixtures import tiny_build
from mailmind.platform import sse, webroot

SCRIPT = f"/{tiny_build.SCRIPT}"


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A build of this test's own, to change."""
    monkeypatch.setattr(webroot, "DIST", tiny_build.write(tmp_path / "dist"))
    return tmp_path / "dist"


@pytest.fixture
def nothing_built(tmp_path, monkeypatch):
    monkeypatch.setattr(webroot, "DIST", str(tmp_path / "nothing-built"))


def test_a_file_outside_the_build_through_a_link_isnt_served(dist, tmp_path):
    outside = tmp_path / "secret.js"
    outside.write_text("secret")
    os.symlink(outside, dist / "assets" / "linked.js")
    assert webroot.asset("/assets/linked.js") is None


def test_a_licence_beside_the_files_isnt_part_of_the_build(dist):
    before = webroot.build()
    (dist / "assets" / "LICENSE.txt").write_text("words")
    assert webroot.build() == before and webroot.asset("/assets/LICENSE.txt") is None


def test_the_page_asks_no_other_host_for_anything():
    for folder, _, names in os.walk(os.path.join(webroot.REPO, "web")):
        if "node_modules" in folder:
            continue
        for name in names:
            if name.endswith((".html", ".css")):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    assert "https://" not in f.read().replace("http://www.w3.org", ""), name


def test_the_source_page_runs_no_inline_script_and_names_what_the_build_fills_in():
    with open(os.path.join(webroot.REPO, "web", "index.html"), encoding="utf-8") as f:
        text = f.read()
    assert '<meta name="csrf" content="__CSRF_TOKEN__">' in text and '<meta name="mailmind-build" content="__BUILD__">' in text
    assert '<meta name="mailmind-place" content="__PLACE__">' in text
    assert '<script src="/assets/theme.js"></script>' in text and "<script>" not in text


def test_a_rebuild_changes_what_a_tab_compares(dist):
    first = webroot.build()
    assert webroot.build() == first
    (dist / "assets" / "app-abc123.js").unlink()
    (dist / "assets" / "app-def456.js").write_text("// the app, rebuilt\n")
    assert webroot.build() != first


def test_a_rebuild_that_changes_only_the_page_changes_it_too(dist):
    first = webroot.build()
    (dist / "index.html").write_text(tiny_build.PAGE + "<!-- another -->")
    assert webroot.build() != first


def test_a_rebuild_that_made_the_same_files_changes_nothing(dist):
    first = webroot.build()
    tiny_build.write(dist)
    os.utime(dist / "index.html", (1, 1))
    assert webroot.build() == first


def test_a_file_a_rebuild_replaced_is_served_at_once_and_one_it_took_away_is_gone(dist):
    assert webroot.asset(SCRIPT).body == b"// the app\n"
    (dist / "assets" / "app-abc123.js").write_text("// the app, again\n")
    assert webroot.asset(SCRIPT).body == b"// the app, again\n"
    (dist / "assets" / "app-abc123.js").unlink()
    assert webroot.asset(SCRIPT) is None


def test_a_restart_or_new_files_change_what_a_tab_compares_and_the_token_itself_never_shows():
    first = webroot.edition("token-of-one-run")
    assert webroot.edition("token-of-one-run") == first
    assert webroot.edition("token-of-the-next-run") != first
    assert first.startswith(webroot.build() + "-") and "token" not in first


def test_the_builds_frame_has_no_id():
    frame = sse.hello("abc123").decode()
    assert frame == 'event: hello\ndata: {"build": "abc123"}\n\n' and "id:" not in frame


def test_with_nothing_built_nothing_raises(nothing_built):
    assert webroot.is_built() is False and webroot.page("t") is None and webroot.asset(SCRIPT) is None
    assert webroot.build() and webroot.edition("t").startswith(webroot.build() + "-")


def test_a_first_build_changes_what_a_tab_compares(nothing_built, tmp_path, monkeypatch):
    before = webroot.build()
    monkeypatch.setattr(webroot, "DIST", tiny_build.write(tmp_path / "dist"))
    assert webroot.is_built() is True and webroot.build() != before
