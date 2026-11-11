"""The one-click unsubscribe POST (`safe_http.one_click_post`), the one request MailMind makes to a
host a message names. It must refuse anything but https, refuse a host if any address it resolves
to is private, loopback, link-local or otherwise internal, pin the connection to the address it
vetted, follow at most one redirect and vet that one too.

Names resolve through a table here, never DNS. A local HTTPS server stands in for the list's
server: its name maps to 127.0.0.1, which the test lets through the vetting by that one address,
and the app's TLS trusts the test CA for the length of a test.
"""
import socket

import pytest
from fixtures import world as w
from mailmind.bootstrap.curation import safe_http


def post(url):
    return safe_http.one_click_post(url)


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


def test_a_cleanup_doesnt_reach_an_internal_address_a_message_names(app, world, nowhere):
    """The way there: a sender's list header, the cleanup's preview and its token, and the real POST."""
    loom = "news@loom.example.test"
    mid = "<issue-300@loom.example.test>"
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID=mid, Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                            List_Unsubscribe="<https://169.254.169.254/latest/meta-data/>",
                                            List_Unsubscribe_Post="List-Unsubscribe=One-Click", Subject="Issue 300"), (w.SEEN,))
    token = app.get(f"/api/cleanup/sender?addr={loom}").json["confirm_token"]
    done = app.post("/api/cleanup/execute", {"from_addr": loom, "do_unsubscribe": True, "confirm_token": token}).json
    assert (done["status"], done["method"]) == ("failed", "one-click")
    assert nowhere == []
