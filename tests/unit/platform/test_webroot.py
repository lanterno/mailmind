"""The page and its files, as the build holds them. Every test has a tiny build (conftest, tests/fixtures/tiny_build.py), and
one that wants nothing built, or to change a file, makes its own."""
import gzip
import http.client
import os

import pytest

from app_client import CSRF
from fixtures import tiny_build
from mailmind.platform import perimeter, sse, webroot

SCRIPT = f"/{tiny_build.SCRIPT}"


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A build of this test's own, to change."""
    monkeypatch.setattr(webroot, "DIST", tiny_build.write(tmp_path / "dist"))
    return tmp_path / "dist"


@pytest.fixture
def nothing_built(tmp_path, monkeypatch):
    monkeypatch.setattr(webroot, "DIST", str(tmp_path / "nothing-built"))


def test_a_file_goes_by_etag_for_good_and_compressed_for_a_browser_that_takes_it(app):
    plain = app.get(SCRIPT)
    assert plain.status == 200 and plain.headers["content-type"].startswith("text/javascript")
    assert plain.headers["cache-control"] == "public, max-age=31536000, immutable" and "content-encoding" not in plain.headers
    source = tiny_build.FILES[tiny_build.SCRIPT].encode()
    assert plain.body == source
    packed = app.get(SCRIPT, headers={"Accept-Encoding": "gzip, deflate"})
    assert packed.headers["content-encoding"] == "gzip" and gzip.decompress(packed.body) == source
    assert packed.headers["etag"] == plain.headers["etag"]
    again = app.get(SCRIPT, headers={"If-None-Match": plain.headers["etag"]})
    assert again.status == 304 and again.body == b"" and again.headers["etag"] == plain.headers["etag"]


def test_a_font_isnt_compressed_again(app):
    r = app.get(f"/{tiny_build.FONT}", headers={"Accept-Encoding": "gzip"})
    assert r.status == 200 and r.headers["content-type"] == "font/woff2" and "content-encoding" not in r.headers


@pytest.mark.parametrize("path", ["/assets/../pyproject.toml", "/assets/%2e%2e/index.html", "/assets/LICENSES/KleeOne.txt",
                                  "/assets/nope.js", "/assets/.hidden.js", "/assets//app-abc123.js",
                                  "/assets/..\\index.js", "/assets/", "/index.html", "/dashboard.html"])
def test_nothing_but_the_pages_files_is_served(app, path):
    assert app.get(path).status == 404


def test_a_file_outside_the_build_through_a_link_isnt_served(dist, tmp_path):
    outside = tmp_path / "secret.js"
    outside.write_text("secret")
    os.symlink(outside, dist / "assets" / "linked.js")
    assert webroot.asset("/assets/linked.js") is None


def test_a_licence_beside_the_files_isnt_part_of_the_build(dist):
    before = webroot.build()
    (dist / "assets" / "LICENSE.txt").write_text("words")
    assert webroot.build() == before and webroot.asset("/assets/LICENSE.txt") is None


def test_the_page_names_its_own_build_and_this_runs_token_and_isnt_stored(app):
    r = app.get("/")
    text = r.body.decode()
    assert f'<meta name="mailmind-build" content="{webroot.edition(CSRF)}">' in text
    assert f'<meta name="csrf" content="{CSRF}">' in text
    assert "__CSRF_TOKEN__" not in text and "__BUILD__" not in text
    assert r.headers["cache-control"] == "no-store"


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


@pytest.mark.parametrize("path", ["/", "/all", "/search", "/m/ada%40example.test/INBOX", "/brief/needs"])
def test_with_nothing_built_home_and_every_place_say_how_to_build_it(app, nothing_built, path):
    r = app.get(path)
    assert r.status == 404 and b"just build-web" in r.body and r.headers["content-type"].startswith("text/plain")
    assert "content-security-policy" not in r.headers


def test_with_nothing_built_the_event_stream_still_opens_and_says_so(app, nothing_built, monkeypatch):
    monkeypatch.setattr(sse, "HEARTBEAT", 0.05)     # a closed stream ends quickly
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    try:
        conn.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{app.port}", "Sec-Fetch-Site": "same-origin"})
        r = conn.getresponse()
        assert r.status == 200
        assert webroot.edition(CSRF).encode() in b"".join(r.readline() for _ in range(6))
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/", "/?m=3", "/all", "/search", "/search?q=x", "/m/ada%40example.test/INBOX", "/brief/needs",
                                  "/brief/topic/Trips%20%26%20travel"])
def test_home_and_every_place_in_it_is_the_app_holding_this_runs_token(app, path):
    r = app.get(path)
    assert r.status == 200 and r.headers["cache-control"] == "no-store" and b'<div id="root">' in r.body
    assert "__CSRF_TOKEN__" not in r.body.decode() and "__BUILD__" not in r.body.decode()
    assert r.headers["content-security-policy"] == perimeter.page_policy()


@pytest.mark.parametrize("path", ["/nextdoor", "/alley", "/searching", "/mail", "/m", "/brief"])
def test_a_path_that_only_starts_like_a_place_isnt_one(app, path):
    assert app.get(path).status == 404


@pytest.mark.parametrize("path, where", [
    ("/next", "/"), ("/next/", "/"), ("/next?m=3", "/?m=3"), ("/next/all", "/all"), ("/next/search?q=x&lane=feed", "/search?q=x&lane=feed"),
    ("/next/m/ada%40example.test/INBOX?m=3", "/m/ada%40example.test/INBOX?m=3"), ("/next/brief/needs", "/brief/needs"),
])
def test_a_link_from_when_the_app_was_at_next_comes_to_the_same_place(app, path, where):
    r = app.get(path)
    assert r.status == 302 and r.headers["location"] == where


@pytest.mark.parametrize("path", ["/next//elsewhere.example", "/next///elsewhere.example", "/next/\\elsewhere.example",
                                  "/next/https://elsewhere.example", "/next/nope", "/next/../elsewhere.example"])
def test_that_redirect_goes_nowhere_but_the_app(app, path):
    r = app.get(path)
    assert r.status == 302 and r.headers["location"] == "/"
