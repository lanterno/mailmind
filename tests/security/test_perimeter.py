"""mailmind.platform.perimeter: the checks run before any route. The app tests
(test_http_get, test_http_post_guards, test_http_events) pin the answers end to end; these pin
each check on its own."""
import email.message

import pytest

from mailmind.platform import perimeter

PORT = 8721
OWN = f"127.0.0.1:{PORT}"
TOKEN = "the-token"
PAGE = {"Host": OWN, "Sec-Fetch-Site": "same-origin"}   # what the page's own fetch sends
POSTED = {**PAGE, "Origin": f"http://{OWN}"}             # and, on a POST, its Origin


def headers(**kw):
    """Request headers as the old server parses them: a lookup that ignores case."""
    m = email.message.Message()
    for k, v in kw.items():
        m[k.replace("_", "-")] = v
    return m


@pytest.mark.parametrize("host", [OWN, f"localhost:{PORT}", f"LOCALHOST:{PORT}"])
def test_this_servers_own_host_passes(host):
    assert perimeter.refuse_host(headers(Host=host), PORT) is None
    assert perimeter.refuse_host(headers(Host=host, Origin=f"http://{host.lower()}"), PORT) is None


@pytest.mark.parametrize("kw", [{}, {"Host": "rebound.example.test"}, {"Host": "127.0.0.1:9999"},
                                {"Host": "127.0.0.1"}, {"Host": OWN, "Origin": "http://rebound.example.test"},
                                {"Host": OWN, "Origin": f"https://{OWN}"}, {"Host": OWN, "Origin": "null"}])
def test_any_other_host_or_origin_is_refused(kw):
    assert perimeter.refuse_host(headers(**kw), PORT) == perimeter.Refusal(403, "bad host/origin")


@pytest.mark.parametrize("site", ["same-origin", "SAME-ORIGIN"])
def test_the_pages_own_api_requests_pass(site):
    assert perimeter.refuse_cross_site(headers(Sec_Fetch_Site=site), "/api/digest") is None


@pytest.mark.parametrize("site", [None, "none"])
def test_an_api_request_that_doesnt_come_from_the_page_is_refused(site):
    """A typed URL says none; a browser too old to say, or a script, says nothing."""
    h = headers(**({"Sec-Fetch-Site": site} if site else {}))
    assert perimeter.refuse_cross_site(h, "/api/digest") == perimeter.Refusal(403, "same-origin request required")
    assert perimeter.refuse_cross_site(h, "/api") is not None


@pytest.mark.parametrize("site", [None, "none", "same-origin"])
def test_the_page_itself_opens_from_a_typed_url_or_an_older_browser(site):
    h = headers(**({"Sec-Fetch-Site": site} if site else {}))
    assert perimeter.refuse_cross_site(h, "/") is None
    assert perimeter.refuse_cross_site(h, "/apical") is None             # not under /api/


def test_a_post_the_page_didnt_make_is_refused_before_its_token_is_looked_at():
    h = headers(Host=OWN, X_CSRF_Token=TOKEN)
    assert perimeter.refuse_mutation("/api/seen", h, port=PORT, token=TOKEN).error == "same-origin request required"


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_read_another_site_starts_is_refused(site):
    h = headers(Sec_Fetch_Site=site, Sec_Fetch_Mode="no-cors", Sec_Fetch_Dest="image")
    assert perimeter.refuse_cross_site(h, "/api/digest") == perimeter.Refusal(403, "cross-site request")


def test_another_site_may_link_to_the_page_but_not_frame_it_or_open_the_api():
    nav = {"Sec_Fetch_Site": "cross-site", "Sec_Fetch_Mode": "navigate"}
    assert perimeter.refuse_cross_site(headers(**nav, Sec_Fetch_Dest="document"), "/") is None
    assert perimeter.refuse_cross_site(headers(**nav, Sec_Fetch_Dest="iframe"), "/")
    assert perimeter.refuse_cross_site(headers(**nav, Sec_Fetch_Dest="document"), "/api/digest")


@pytest.mark.parametrize("length, refused", [(None, False), ("0", False), ("1000", False), ("1001", True),
                                             ("-1", True), ("lots", True)])
def test_a_body_is_judged_by_its_declared_length(length, refused):
    h = headers(**({"Content-Length": length} if length is not None else {}))
    got = perimeter.refuse_body(h, 1000)
    assert got == (perimeter.Refusal(413, "payload too large") if refused else None)


def test_a_post_needs_the_host_then_the_token():
    ok = {**POSTED, "X-CSRF-Token": TOKEN}
    assert perimeter.refuse_mutation("/api/seen", headers(**ok), port=PORT, token=TOKEN) is None
    assert perimeter.refuse_mutation("/api/seen", headers(Host="evil.example.test", X_CSRF_Token="nope"),
                                     port=PORT, token=TOKEN).error == "bad host/origin"
    for token in (None, "nope", TOKEN + "x"):
        h = headers(**POSTED, **({"X-CSRF-Token": token} if token else {}))
        assert perimeter.refuse_mutation("/api/seen", h, port=PORT, token=TOKEN) == \
            perimeter.Refusal(403, "bad or missing CSRF token")


@pytest.mark.parametrize("path", ["/api/seen", "/api/send"])
def test_every_post_also_needs_this_servers_origin(path):
    """A browser sends an Origin with every POST; one without came from no page."""
    ok = {**PAGE, "X-CSRF-Token": TOKEN}
    assert perimeter.refuse_mutation(path, headers(**ok), port=PORT, token=TOKEN) == \
        perimeter.Refusal(403, "origin required")
    for origin in (f"http://{OWN}", f"http://localhost:{PORT}"):
        assert perimeter.refuse_mutation(path, headers(**ok, Origin=origin), port=PORT, token=TOKEN) is None


def test_a_post_is_judged_by_its_size_before_its_guards():
    h = headers(Host="evil.example.test", Content_Length="1001")
    assert perimeter.refuse_post("/api/seen", h, port=PORT, token=TOKEN, cap=1000).status == 413
    assert perimeter.refuse_post("/api/seen", headers(Host="evil.example.test"), port=PORT, token=TOKEN,
                                 cap=1000).status == 403


def test_a_get_is_judged_by_its_host_before_where_it_came_from():
    h = headers(Host="evil.example.test", Sec_Fetch_Site="cross-site")
    assert perimeter.refuse_get("/api/digest", h, port=PORT).error == "bad host/origin"
    assert perimeter.refuse_get("/api/digest", headers(Host=OWN, Sec_Fetch_Site="cross-site"),
                                port=PORT).error == "cross-site request"


def test_every_answer_says_what_its_body_is_and_keeps_out_of_other_pages():
    got = dict(perimeter.answer_headers("/"))
    assert {k: got.pop(k) for k in ("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options",
                                    "Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy")} == {
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin"}
    policy = dict(f.split("=") for f in got.pop("Permissions-Policy").split(", "))
    assert set(policy.values()) == {"()"} and {"camera", "microphone", "geolocation", "payment"} <= set(policy)
    assert got == {}


def test_no_cache_keeps_an_api_answer():
    assert dict(perimeter.answer_headers("/api/digest")) == {**dict(perimeter.answer_headers("/")),
                                                             "Cache-Control": "no-store"}
    assert "Cache-Control" not in dict(perimeter.answer_headers("/"))


def policy_of():
    return dict(d.split(" ", 1) for d in perimeter.page_policy().split("; "))


def test_only_script_files_from_this_server_may_run():
    assert policy_of()["script-src"] == "'self'"


def test_the_page_reaches_only_this_server_and_no_one_may_frame_it():
    policy = policy_of()
    assert {k: policy[k] for k in ("default-src", "connect-src", "frame-ancestors", "base-uri", "form-action",
                                   "object-src")} == {
        "default-src": "'none'", "connect-src": "'self'", "frame-ancestors": "'none'", "base-uri": "'none'",
        "form-action": "'none'", "object-src": "'none'"}


def test_the_page_keeps_its_policy_word_for_word_which_allows_nothing_for_mail():
    assert perimeter.page_policy() == (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src data:; "
        "font-src 'self'; connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'")


def test_every_refusal_answers_in_one_shape():
    assert perimeter.Refusal(403, "cross-site request").answer() == {"ok": False, "error": "cross-site request"}
