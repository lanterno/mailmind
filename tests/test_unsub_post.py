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
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen.append({"path": self.path, "host": self.headers["Host"],
                                 "content_type": self.headers["Content-Type"],
                                 "user_agent": self.headers["User-Agent"], "cookie": self.headers["Cookie"],
                                 "body": body})
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
                   "body": b"List-Unsubscribe=One-Click"}


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
                                  "v6-loopback.example.test", "mapped.example.test", "cgnat.example.test"])
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
    ("8.8.8.8", False), ("1.1.1.1", False), ("2606:4700:4700::1111", False),
])
def test_which_addresses_count_as_internal(ip, forbidden):
    assert safe_http.ip_is_forbidden(ip) is forbidden
