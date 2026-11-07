"""The page and its files, from the source or a build, through both front doors."""
import gzip
import os
import time

import pytest

from app_client import CSRF
from mailmind.platform import perimeter, sse, webroot

SCRIPT = "/assets/legacy/app.js"


@pytest.fixture(autouse=True)
def no_build(tmp_path, monkeypatch):
    """A build on this machine is left out, unless a test makes one."""
    monkeypatch.setattr(webroot, "DIST", str(tmp_path / "no-build"))


def test_a_file_goes_by_etag_and_compressed_for_a_browser_that_takes_it(app):
    plain = app.get(SCRIPT)
    assert plain.status == 200 and plain.headers["content-type"].startswith("text/javascript")
    assert plain.headers["cache-control"] == "no-cache" and "content-encoding" not in plain.headers
    source = open(os.path.join(webroot.SOURCE, "assets", "legacy", "app.js"), "rb").read()
    assert plain.body == source
    packed = app.get(SCRIPT, headers={"Accept-Encoding": "gzip, deflate"})
    assert packed.headers["content-encoding"] == "gzip" and gzip.decompress(packed.body) == source
    assert packed.headers["etag"] == plain.headers["etag"]
    again = app.get(SCRIPT, headers={"If-None-Match": plain.headers["etag"]})
    assert again.status == 304 and again.body == b"" and again.headers["etag"] == plain.headers["etag"]


def test_a_font_isnt_compressed_again(app):
    name = next(n for n in os.listdir(os.path.join(webroot.SOURCE, "assets", "fonts")) if n.endswith(".woff2"))
    r = app.get(f"/assets/fonts/{name}", headers={"Accept-Encoding": "gzip"})
    assert r.status == 200 and r.headers["content-type"] == "font/woff2" and "content-encoding" not in r.headers


@pytest.mark.parametrize("path", ["/assets/../server.py", "/assets/%2e%2e/server.py", "/assets/fonts/LICENSES/KleeOne.txt",
                                  "/assets/legacy/nope.js", "/assets/.hidden.js", "/assets/legacy//app.js",
                                  "/assets/..\\server.js", "/assets/", "/dashboard.html"])
def test_nothing_but_the_pages_files_is_served(app, path):
    assert app.get(path).status == 404


def test_a_file_outside_the_folder_through_a_link_isnt_served(tmp_path, monkeypatch):
    home = tmp_path / "web"
    (home / "assets").mkdir(parents=True)
    (home / "index.html").write_text("page")
    outside = tmp_path / "secret.js"
    outside.write_text("secret")
    os.symlink(outside, home / "assets" / "linked.js")
    monkeypatch.setattr(webroot, "SOURCE", str(home))
    monkeypatch.setattr(webroot, "DIST", str(tmp_path / "no-build"))
    assert webroot.asset("/assets/linked.js") is None


def test_the_page_names_its_own_build_and_this_runs_token_and_isnt_stored(app):
    r = app.get("/")
    text = r.body.decode()
    assert f'<meta name="mailmind-build" content="{webroot.edition(CSRF)}">' in text
    assert "__CSRF_TOKEN__" not in text and "__BUILD__" not in text
    assert r.headers["cache-control"] == "no-store"
    assert "googleapis" not in text and "<script>" not in text


def test_the_page_asks_no_other_host_for_anything():
    for folder, _, names in os.walk(webroot.SOURCE):
        if "node_modules" in folder:
            continue
        for name in names:
            if name.endswith((".html", ".css")):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    assert "https://" not in f.read().replace("http://www.w3.org", ""), name


@pytest.fixture
def two_roots(tmp_path, monkeypatch):
    source, dist = tmp_path / "web", tmp_path / "dist"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("<p>source __BUILD__")
    (source / "assets" / "a.js").write_text("source()")
    monkeypatch.setattr(webroot, "SOURCE", str(source))
    monkeypatch.setattr(webroot, "DIST", str(dist))
    return source, dist


def built(dist):
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<p>built __BUILD__")
    (dist / "assets" / "a-abc.js").write_text("built()")
    os.utime(dist / "index.html", (time.time() + 5, time.time() + 5))


def test_with_no_build_the_source_is_served(two_roots):
    assert webroot.page("t").startswith("<p>source") and webroot.asset("/assets/a.js").body == b"source()"


def test_a_build_is_served_for_good_with_its_files_named_by_content(two_roots):
    _, dist = two_roots
    built(dist)
    assert webroot.page("t").startswith("<p>built")
    assert webroot.asset("/assets/a-abc.js").cache == "public, max-age=31536000, immutable"
    assert webroot.asset("/assets/a.js") is None


def test_a_source_edited_after_the_build_wins(two_roots):
    source, dist = two_roots
    built(dist)
    os.utime(source / "assets" / "a.js", (time.time() + 60, time.time() + 60))
    assert webroot.page("t").startswith("<p>source") and webroot.asset("/assets/a.js").cache == "no-cache"


def test_the_build_changes_with_what_is_served_and_only_then(two_roots):
    source, dist = two_roots
    first = webroot.build()
    assert webroot.build() == first
    (source / "assets" / "a.js").write_text("source(2)")
    second = webroot.build()
    assert second != first
    built(dist)
    assert webroot.build() != second


def test_an_edit_of_a_file_is_served_at_once(two_roots):
    source, _ = two_roots
    assert webroot.asset("/assets/a.js").body == b"source()"
    (source / "assets" / "a.js").write_text("source(two)")
    assert webroot.asset("/assets/a.js").body == b"source(two)"


def test_a_restart_or_new_files_change_what_a_tab_compares_and_the_token_itself_never_shows():
    first = webroot.edition("token-of-one-run")
    assert webroot.edition("token-of-one-run") == first
    assert webroot.edition("token-of-the-next-run") != first
    assert first.startswith(webroot.build() + "-") and "token" not in first


def test_the_builds_frame_has_no_id():
    frame = sse.hello("abc123").decode()
    assert frame == 'event: hello\ndata: {"build": "abc123"}\n\n' and "id:" not in frame


def test_a_place_in_the_app_says_how_to_build_it_when_nothing_has(app):
    r = app.get("/all")
    assert r.status == 404 and b"just build-web" in r.body


@pytest.fixture
def with_next(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "next.html").write_text('<meta name="csrf" content="__CSRF_TOKEN__"><meta content="__BUILD__"><div id="root"></div>')
    (dist / "assets" / "next-abc.js").write_text("// the app\n")
    monkeypatch.setattr(webroot, "DIST", str(dist))


@pytest.mark.parametrize("path", ["/", "/?m=3", "/all", "/search", "/search?q=x", "/m/ada%40example.test/INBOX", "/brief/needs",
                                  "/brief/topic/Trips%20%26%20travel"])
def test_home_and_every_place_in_it_is_the_app_holding_this_runs_token(app, with_next, path):
    r = app.get(path)
    assert r.status == 200 and r.headers["cache-control"] == "no-store" and b'<div id="root">' in r.body
    assert "__CSRF_TOKEN__" not in r.body.decode() and "__BUILD__" not in r.body.decode()
    assert r.headers["content-security-policy"] == perimeter.next_page_policy()


@pytest.mark.parametrize("path", ["/nextdoor", "/alley", "/searching", "/mail", "/m", "/brief", "/legacy/x"])
def test_a_path_that_only_starts_like_a_place_isnt_one(app, with_next, path):
    assert app.get(path).status == 404


@pytest.mark.parametrize("path, where", [
    ("/next", "/"), ("/next/", "/"), ("/next?m=3", "/?m=3"), ("/next/all", "/all"), ("/next/search?q=x&lane=feed", "/search?q=x&lane=feed"),
    ("/next/m/ada%40example.test/INBOX?m=3", "/m/ada%40example.test/INBOX?m=3"), ("/next/brief/needs", "/brief/needs"),
])
def test_a_link_from_when_the_app_was_at_next_comes_to_the_same_place(app, with_next, path, where):
    r = app.get(path)
    assert r.status == 302 and r.headers["location"] == where


@pytest.mark.parametrize("path", ["/next//elsewhere.example", "/next///elsewhere.example", "/next/\\elsewhere.example",
                                  "/next/https://elsewhere.example", "/next/nope", "/next/../elsewhere.example"])
def test_that_redirect_goes_nowhere_but_the_app(app, path):
    r = app.get(path)
    assert r.status == 302 and r.headers["location"] == "/"


def test_the_old_page_stays_at_legacy_under_its_own_policy(app, with_next):
    r = app.get("/legacy")
    assert r.status == 200 and b'<div id="root">' not in r.body and b'<meta name="csrf"' in r.body
    assert r.headers["content-security-policy"] == perimeter.page_policy()


def test_an_app_file_comes_from_the_build_though_the_page_comes_from_source(app, with_next):
    assert not os.path.exists(os.path.join(webroot.DIST, "index.html"))
    assert webroot._root() == webroot.SOURCE
    r = app.get("/assets/next-abc.js")
    assert r.status == 200 and b"the app" in r.body and "immutable" in r.headers["cache-control"]
    assert app.get(SCRIPT).status == 200


def test_with_nothing_built_the_old_page_stands_in_at_home(app):
    r = app.get("/")
    assert r.status == 200 and r.headers["content-security-policy"] == perimeter.page_policy()
    assert app.get("/legacy").body == r.body