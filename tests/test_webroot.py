"""The page and its files, from the source or a build, through both front doors."""
import gzip
import os
import time

import pytest

from mailmind.platform import webroot

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
    assert f'<meta name="mailmind-build" content="{webroot.build()}">' in text
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
