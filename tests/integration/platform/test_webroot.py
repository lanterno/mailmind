"""The page and its files, as the build holds them. Every test has a tiny build (conftest, tests/fixtures/tiny_build.py), and
one that wants nothing built, or to change a file, makes its own."""
import gzip
import http.client

import pytest

import problems
from app_client import CSRF
from fixtures import tiny_build
from mailmind.http import shared
from mailmind.platform import perimeter, sse, webroot

SCRIPT = f"/{tiny_build.SCRIPT}"


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


def test_the_page_names_its_own_build_and_this_runs_token_and_isnt_stored(app):
    r = app.get("/")
    text = r.body.decode()
    assert f'<meta name="mailmind-build" content="{webroot.edition(CSRF)}">' in text
    assert f'<meta name="csrf" content="{CSRF}">' in text
    assert "__CSRF_TOKEN__" not in text and "__BUILD__" not in text
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/all", "/all/__snoozed__", "/documents", "/search", "/m/ada%40example.test/INBOX", "/brief/needs"])
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


@pytest.mark.parametrize("path", ["/", "/?m=3", "/all", "/all/__snoozed__", "/documents", "/search", "/search?q=x", "/m/ada%40example.test/INBOX", "/brief/needs",
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
    ("/next", "/"), ("/next/", "/"), ("/next?m=3", "/?m=3"), ("/next/all", "/all"), ("/next/documents", "/documents"), ("/next/search?q=x&lane=feed", "/search?q=x&lane=feed"),
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


@pytest.mark.parametrize("path, where", [
    ("/next?m=3&m=4&blank=&x=%20y%26z", "/?m=3&m=4&blank=&x=%20y%26z"), ("/next/search?q=a+b&q=c", "/search?q=a+b&q=c"), ("/next/all?", "/all"),
    ("/next//elsewhere.example?m=3&m=4", "/?m=3&m=4"),
])
def test_that_redirect_carries_the_query_as_it_was_sent(app, path, where):
    """Every other route reads each name's first value, and a blank as none."""
    r = app.get(path)
    assert (r.status, r.body, r.headers["location"]) == (302, b"", where) and r.headers["content-type"] == "text/plain"


def test_a_file_is_the_same_file_to_every_cache_whichever_way_it_was_asked_for(app):
    plain = app.get(SCRIPT)
    packed = app.get(SCRIPT, headers={"Accept-Encoding": "gzip"})
    unchanged = app.get(SCRIPT, headers={"If-None-Match": plain.headers["etag"]})
    assert plain.headers["vary"] == packed.headers["vary"] == unchanged.headers["vary"] == "Accept-Encoding"
    assert unchanged.headers["cache-control"] == plain.headers["cache-control"] and "content-length" not in unchanged.headers
    assert app.get(SCRIPT, headers={"If-None-Match": 'W/"another"'}).status == 200


@pytest.mark.parametrize("path", ["/assets/app%2Dabc123.js", "/%61ssets/app-abc123.js", "/assets/%2e%2e%2f%2e%2e%2fpyproject.toml", "/assets/..%5cindex.js",
                                  "/assets/%2E%2E/index.html", "/assets/app-abc123.js/", "/assets/./app-abc123.js", "/assets/styles/../app-abc123.js",
                                  "/assets/app-abc123.js%00"])
def test_a_file_is_found_by_its_path_as_sent_and_never_decoded_into_another(app, path):
    r = app.get(path)
    assert (r.status, r.body) == (404, b"not found")


@pytest.mark.parametrize("path", ["/", "/all", "/search", "/m/ada%40example.test/INBOX", "/brief/needs", "/next", "/next/all", SCRIPT, "/assets/nope.js", "/nope"])
def test_opening_the_page_or_a_file_isnt_someone_here(app, monkeypatch, path):
    """A tab left open reloading, or a link opened from elsewhere, shouldn't keep drafting ahead going."""
    seen = []
    monkeypatch.setattr(shared, "_someone_here", lambda: seen.append(path))
    assert app.get(path, headers={"Sec-Fetch-Site": "none"}).status in (200, 302, 404)
    assert seen == []


@pytest.mark.parametrize("path", ["/", "/all", "/next", SCRIPT])
def test_a_head_gets_only_the_status_it_always_got(app, path):
    r = app.request("HEAD", path)
    assert (r.status, r.body) == (501, b"")


def test_a_failure_reading_the_page_is_the_fixed_failure(app, monkeypatch, capsys):
    def boom(token):
        raise OSError(5, "hunter2")
    monkeypatch.setattr(webroot, "page", boom)
    r = app.get("/")
    assert (r.status, r.json) == (500, problems.FAILED)
    assert "hunter2" not in capsys.readouterr().out
