"""The one request MailMind makes to an address a message named: RFC 8058's one-click unsubscribe. Whoever wrote
the message chose the URL, so it's vetted as hostile."""
import http.client
import ipaddress
import socket
import ssl
import urllib.parse

from mailmind.platform import profile
from mailmind.platform.db import txguard

USER_AGENT = "MailMind-Unsub/1.0 (+list-unsubscribe one-click)"
TIMEOUT = 10
READ_CAP = 65536


def ip_is_forbidden(ip):
    """Reject anything that could reach the local network / metadata endpoints (SSRF)."""
    try:
        a = ipaddress.ip_address(ip)
    except Exception:
        return True
    if (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
            or a.is_reserved or a.is_unspecified):
        return True
    if a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"):   # CGNAT
        return True
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped and ip_is_forbidden(str(a.ipv4_mapped)):
        return True
    return False


def one_click_post(url, _redirects=1):
    """SSRF-hardened RFC 8058 one-click POST. https-only; every resolved A/AAAA vetted; connect to the
    vetted IP with SNI/Host pinned to the hostname (defeats DNS-rebind between check and connect);
    <=1 re-vetted redirect; hard timeout; read cap; no cookies/creds/ambient auth. Returns
    (ok, http_status, reason). Runs OUTSIDE the IMAP gate so a hanging remote can't stall IMAP."""
    profile.refuse_writes("an unsubscribe request")
    txguard.refuse_network("an unsubscribe request")     # its DNS lookup included
    p = urllib.parse.urlparse(url or "")
    if p.scheme != "https" or not p.hostname:
        return False, 0, "bad_scheme"
    host, port = p.hostname, (p.port or 443)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return False, 0, "dns"
    vetted = None
    for family, _t, _pr, _cn, sa in infos:
        ip = sa[0]
        if ip_is_forbidden(ip):
            return False, 0, "ssrf_blocked"        # ANY resolved IP private → refuse the whole host
        vetted = (ip, family)
    if not vetted:
        return False, 0, "dns"
    ip, family = vetted
    body = b"List-Unsubscribe=One-Click"
    path = (p.path or "/") + (("?" + p.query) if p.query else "")
    ctx = ssl.create_default_context()
    raw_sock = None
    try:
        raw_sock = socket.socket(family, socket.SOCK_STREAM)
        raw_sock.settimeout(TIMEOUT)
        raw_sock.connect((ip, port))
        tls = ctx.wrap_socket(raw_sock, server_hostname=host)      # SNI = hostname, socket = vetted IP
        conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT)
        conn.sock = tls
        conn.request("POST", path, body=body, headers={
            "Host": host, "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)), "User-Agent": USER_AGENT, "Connection": "close"})
        resp = conn.getresponse()
        status = resp.status
        loc = resp.getheader("Location") if status in (301, 302, 303, 307, 308) else None
        try:
            resp.read(READ_CAP)
        except Exception:
            pass
        conn.close()
    except Exception:
        return False, 0, "timeout"
    finally:
        try:
            if raw_sock is not None:
                raw_sock.close()
        except Exception:
            pass
    if 200 <= status < 300:
        return True, status, "ok"
    if loc and _redirects > 0:
        return one_click_post(urllib.parse.urljoin(url, loc), _redirects - 1)   # re-vetted
    return False, status, "bad_status"
