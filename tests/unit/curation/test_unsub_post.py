"""The one-click unsubscribe POST (`safe_http.one_click_post`), the one request MailMind makes to a
host a message names. It must refuse anything but https, refuse a host if any address it resolves
to is private, loopback, link-local or otherwise internal, pin the connection to the address it
vetted, follow at most one redirect and vet that one too.

Names resolve through a table here, never DNS. A local HTTPS server stands in for the list's
server: its name maps to 127.0.0.1, which the test lets through the vetting by that one address,
and the app's TLS trusts the test CA for the length of a test.
"""
import http.server
import socket
import ssl
import threading

import pytest
import trustme
from mailmind.bootstrap.curation import safe_http

HOST = "unsub.example.test"

NAMES = {
    HOST: ["127.0.0.1"],
    "private.example.test": ["10.0.0.5"],
    "metadata.example.test": ["169.254.169.254"],
    "mixed.example.test": ["8.8.8.8", "192.168.1.20"],
    "v6-loopback.example.test": ["::1"],
    "mapped.example.test": ["::ffff:10.0.0.5"],
    "cgnat.example.test": ["100.64.0.9"],
    "mixed-first.example.test": ["192.168.1.20", "8.8.8.8"],
    "mapped-metadata.example.test": ["::ffff:169.254.169.254"],
    "mapped-cgnat.example.test": ["::ffff:100.64.0.9"],
    "link-local.example.test": ["fe80::1"],
    "any.example.test": ["0.0.0.0"],
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen.append({"path": self.path, "host": self.headers["Host"],
                                 "content_type": self.headers["Content-Type"],
                                 "user_agent": self.headers["User-Agent"], "cookie": self.headers["Cookie"],
                                 "authorization": self.headers["Authorization"], "body": body})
        status, location = self.server.routes.get(self.path.split("?")[0], (404, None))
        self.send_response(status)
        if location:
            self.send_header("Location", location.format(port=self.server.server_address[1]))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def ca():
    return trustme.CA()


@pytest.fixture
def lists(ca, monkeypatch):
    """The list's HTTPS server, with its name resolving to it and the app trusting its cert."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert(HOST).configure_cert(ctx)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.seen, srv.routes = [], {}
    srv.handle_error = lambda *a: None       # a client that leaves with nothing said is what some of these are about
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, name="fake-https",
                     daemon=True).start()

    real_getaddrinfo, real_context = socket.getaddrinfo, ssl.create_default_context

    def getaddrinfo(host, port, *a, **kw):
        if host in NAMES:
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     (ip, port) if "." in ip and ":" not in ip else (ip, port, 0, 0)) for ip in NAMES[host]]
        if host.endswith(".test"):
            raise socket.gaierror(socket.EAI_NONAME, "unknown name")
        return real_getaddrinfo(host, port, *a, **kw)

    def context(*a, **kw):
        c = real_context(*a, **kw)
        ca.configure_trust(c)
        return c

    real_forbidden = safe_http.ip_is_forbidden
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(ssl, "create_default_context", context)
    monkeypatch.setattr(safe_http, "ip_is_forbidden", lambda ip: ip != "127.0.0.1" and real_forbidden(ip))
    srv.url = lambda path: f"https://{HOST}:{srv.server_address[1]}{path}"
    yield srv
    srv.shutdown()
    srv.server_close()


def post(url):
    return safe_http.one_click_post(url)


def test_a_one_click_post_says_only_what_rfc_8058_asks(lists):
    lists.routes["/u/ada-218"] = (200, None)
    assert post(lists.url("/u/ada-218?list=weekly")) == (True, 200, "ok")
    [req] = lists.seen
    assert req == {"path": "/u/ada-218?list=weekly", "host": HOST,
                   "content_type": "application/x-www-form-urlencoded",
                   "user_agent": "MailMind-Unsub/1.0 (+list-unsubscribe one-click)", "cookie": None,
                   "authorization": None, "body": b"List-Unsubscribe=One-Click"}


def test_a_status_that_isnt_success_fails(lists):
    lists.routes["/gone"] = (410, None)
    assert post(lists.url("/gone")) == (False, 410, "bad_status")


def test_one_redirect_is_followed_and_a_second_is_not(lists):
    lists.routes.update({"/hop": (302, "https://unsub.example.test:{port}/done"), "/done": (200, None),
                         "/hop-hop": (307, "/hop")})
    assert post(lists.url("/hop")) == (True, 200, "ok")
    assert [r["path"] for r in lists.seen] == ["/hop", "/done"]
    lists.seen.clear()
    assert post(lists.url("/hop-hop")) == (False, 302, "bad_status")
    assert [r["path"] for r in lists.seen] == ["/hop-hop", "/hop"]


def test_a_redirect_to_an_internal_host_is_refused(lists):
    lists.routes["/sneaky"] = (302, "https://private.example.test/admin")
    assert post(lists.url("/sneaky")) == (False, 0, "ssrf_blocked")
    assert [r["path"] for r in lists.seen] == ["/sneaky"]


def test_a_redirect_to_plain_http_is_refused(lists):
    lists.routes["/downgrade"] = (301, "http://unsub.example.test/u")
    assert post(lists.url("/downgrade")) == (False, 0, "bad_scheme")


@pytest.mark.parametrize("host", ["private.example.test", "metadata.example.test", "mixed.example.test",
                                  "v6-loopback.example.test", "mapped.example.test", "cgnat.example.test",
                                  "mixed-first.example.test", "mapped-metadata.example.test", "mapped-cgnat.example.test",
                                  "link-local.example.test", "any.example.test"])
def test_a_host_with_any_internal_address_is_refused_before_connecting(lists, host):
    assert post(f"https://{host}/u") == (False, 0, "ssrf_blocked")
    assert lists.seen == []


@pytest.mark.parametrize("url", ["http://unsub.example.test/u", "mailto:u@example.test", "", "https:///nohost",
                                 "ftp://unsub.example.test/u"])
def test_anything_but_https_is_refused(lists, url):
    assert post(url) == (False, 0, "bad_scheme")


def test_a_name_that_doesnt_resolve(lists):
    assert post("https://nowhere.example.test/u") == (False, 0, "dns")


def test_a_certificate_for_another_name_fails(lists, monkeypatch):
    monkeypatch.setitem(NAMES, "other.example.test", ["127.0.0.1"])      # the server's cert names HOST only
    lists.routes["/u"] = (200, None)
    assert post(f"https://other.example.test:{lists.server_address[1]}/u") == (False, 0, "timeout")
    assert lists.seen == []


@pytest.mark.parametrize("ip,forbidden", [
    ("10.0.0.1", True), ("172.16.5.4", True), ("192.168.0.1", True), ("127.0.0.1", True),
    ("169.254.169.254", True), ("100.64.0.1", True), ("0.0.0.0", True), ("224.0.0.1", True),
    ("240.0.0.1", True), ("::1", True), ("fe80::1", True), ("fc00::1", True), ("::", True),
    ("::ffff:10.0.0.1", True), ("::ffff:127.0.0.1", True), ("not an address", True),
    ("100.127.255.255", True), ("198.18.0.1", True), ("192.0.2.1", True), ("255.255.255.255", True),
    ("::127.0.0.1", True), ("64:ff9b::7f00:1", True), ("::ffff:100.64.0.1", True), ("::ffff:169.254.169.254", True),
    ("fd00::1", True), ("ff02::1", True), ("2001:db8::1", True),
    ("2002:7f00:1::", True), ("2002:a00:5::", True), ("2002:a9fe:a9fe::", True), ("2002:6440:9::", True),   # 6to4, carrying each
    ("8.8.8.8", False), ("1.1.1.1", False), ("2606:4700:4700::1111", False),
    ("100.63.255.255", False), ("100.128.0.1", False), ("::ffff:8.8.8.8", False),
])
def test_which_addresses_count_as_internal(ip, forbidden):
    assert safe_http.ip_is_forbidden(ip) is forbidden


# ── a name whose answer changes ──────────────────────────────────────────────────────────────────

def rebinding(monkeypatch, *answers):
    """HOST answers differently each time it's asked, as a hostile DNS server's name does, and stays on its last answer."""
    asked, others = [], socket.getaddrinfo

    def getaddrinfo(host, port, *a, **kw):
        if host != HOST:
            return others(host, port, *a, **kw)
        asked.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[min(len(asked), len(answers)) - 1], port))]
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return asked


def connections_to(monkeypatch):
    made, real = [], socket.socket

    class Noting(real):
        def connect(self, address):
            made.append(address)
            return super().connect(address)
    monkeypatch.setattr(socket, "socket", Noting)
    return made


def test_a_name_is_asked_once_and_the_connection_goes_where_the_answer_vetted_said(lists, monkeypatch):
    asked, made = rebinding(monkeypatch, "127.0.0.1", "10.0.0.5"), connections_to(monkeypatch)
    lists.routes["/u"] = (200, None)
    assert post(lists.url("/u")) == (True, 200, "ok")
    assert asked == [HOST]
    assert made == [("127.0.0.1", lists.server_address[1])]
    assert [r["host"] for r in lists.seen] == [HOST]


def test_a_redirect_back_to_a_name_that_now_answers_internally_is_refused(lists, monkeypatch):
    asked = rebinding(monkeypatch, "127.0.0.1", "10.0.0.5")
    lists.routes.update({"/hop": (302, "https://unsub.example.test:{port}/done"), "/done": (200, None)})
    assert post(lists.url("/hop")) == (False, 0, "ssrf_blocked")
    assert len(asked) == 2 and [r["path"] for r in lists.seen] == ["/hop"]


def test_a_name_that_answers_internally_first_gets_no_second_question(lists, monkeypatch):
    asked = rebinding(monkeypatch, "10.0.0.5", "127.0.0.1")
    assert post(lists.url("/u")) == (False, 0, "ssrf_blocked")
    assert len(asked) == 1 and lists.seen == []


# ── an address in the URL itself ─────────────────────────────────────────────────────────────────

@pytest.fixture
def nowhere(monkeypatch):
    """Names resolve as the machine's own resolver reads a number, in each of its spellings, with no DNS. The vetting here is the
    real one, and a connection to port 443 is noted and goes no further: what it lists is where the POST would have gone."""
    made, real = [], socket.socket

    def answer(host, port, *a, **kw):
        if host == "localhost":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]
        try:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                     (socket.inet_ntop(socket.AF_INET6, socket.inet_pton(socket.AF_INET6, host)), port, 0, 0))]
        except OSError:
            pass
        try:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (socket.inet_ntoa(socket.inet_aton(host)), port))]
        except OSError:
            raise socket.gaierror(socket.EAI_NONAME, "unknown name") from None

    class Noting(real):
        def connect(self, address):
            if address[1] in (443, 8443):
                made.append(address[:2])
                raise OSError("no further, in a test")
            return super().connect(address)
    monkeypatch.setattr(socket, "getaddrinfo", answer)
    monkeypatch.setattr(socket, "socket", Noting)
    return made


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/u", "https://127.1/u", "https://2130706433/u", "https://0x7f.0.0.1/u", "https://0177.0.0.1/u", "https://0/u",
    "https://localhost/u", "https://LOCALHOST/u", "https://[::1]/u", "https://[::ffff:127.0.0.1]/u", "https://[::ffff:7f00:1]/u",
    "https://169.254.169.254/latest/meta-data/", "https://0xa9fea9fe/latest/meta-data/", "https://10.0.0.5:8443/u",
    "https://100.64.0.1/u", "https://[fe80::1]/u", "https://[fc00::1]/u",
    "https://unsub.example.test@127.0.0.1/u", "https://user:secret@10.0.0.5/u", "https://8.8.8.8@169.254.169.254/u",
])
def test_an_internal_address_in_a_url_is_refused_however_it_is_written(nowhere, url):
    assert post(url) == (False, 0, "ssrf_blocked")
    assert nowhere == []


@pytest.mark.parametrize("url, there", [("https://8.8.8.8/u", ("8.8.8.8", 443)), ("https://134744072/u", ("8.8.8.8", 443)),
                                        ("https://127.0.0.1@8.8.8.8:8443/u", ("8.8.8.8", 8443))])
def test_a_public_address_gets_as_far_as_its_own_door(nowhere, url, there):
    assert post(url) == (False, 0, "timeout")
    assert nowhere == [there]


@pytest.mark.parametrize("location", ["https://169.254.169.254/latest/meta-data/", "https://10.0.0.1/", "https://[::1]/", "https://127.0.0.2/",
                                      "https://[::ffff:10.0.0.1]/"])
def test_a_redirect_to_an_internal_address_is_refused(lists, monkeypatch, location):
    host = location.split("//")[1].split("/")[0].strip("[]")
    monkeypatch.setitem(NAMES, host, [host])
    lists.routes["/sneaky"] = (302, location)
    assert post(lists.url("/sneaky")) == (False, 0, "ssrf_blocked")
    assert [r["path"] for r in lists.seen] == ["/sneaky"]


def test_a_login_in_a_url_goes_nowhere(lists):
    lists.routes["/u"] = (200, None)
    for login in ("user:secret", "127.0.0.1", "unsub.example.test"):
        assert post(f"https://{login}@{HOST}:{lists.server_address[1]}/u") == (True, 200, "ok")
    assert [(r["host"], r["authorization"], r["path"]) for r in lists.seen] == [(HOST, None, "/u")] * 3


@pytest.mark.parametrize("path", ["/u\r\nX-Sneaked: 1", "/u\nX-Sneaked: 1", "/u X-Sneaked: 1", "/u\x00"])
def test_a_url_cant_write_a_line_of_its_own_request(lists, path):
    assert post(lists.url(path))[0] is False
    assert lists.seen == []
