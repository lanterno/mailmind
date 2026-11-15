"""A nameserver on a loopback port that answers what MailMind asks of one: a domain's MX records, over UDP.

It answers from its table (`mx`), each host as an MX record of its own, the record's owner a pointer to the question's
name, as nameservers write them. A domain not in the table doesn't exist (NXDOMAIN), one `failing` answers SERVFAIL, and
one `silent` gets no answer at all. Every question it is asked is kept in `asked`, as (domain, record type).

    with FakeDNSServer() as dns:
        dns.mx("acme.example.test", ["aspmx.l.google.com", "alt1.aspmx.l.google.com"])
        dns.address  # ("127.0.0.1", port)
"""
import socket
import struct
import threading

MX, IN = 15, 1


def _name(labels):
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\0"


def _question(data):
    """The question's name, as its labels, its type, and where the question ends."""
    labels, at = [], 12
    while data[at]:
        labels.append(data[at + 1:at + 1 + data[at]].decode("ascii"))
        at += 1 + data[at]
    kind, _ = struct.unpack_from(">HH", data, at + 1)
    return labels, kind, at + 5


class FakeDNSServer:
    def __init__(self):
        self.table = {}
        self.failing = set()
        self.silent = set()
        self.asked = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self.address = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, name="fake-dns", daemon=True)

    def mx(self, domain, hosts):
        """`domain`'s MX records name `hosts`, in this order; none at all for an empty list."""
        self.table[domain.lower()] = list(hosts)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._sock.close()
        self._thread.join(5)

    def _serve(self):
        while True:
            try:
                data, peer = self._sock.recvfrom(4096)
            except OSError:
                return
            try:
                reply = self._answer(data)
            except (IndexError, struct.error, UnicodeDecodeError):
                continue
            if reply is not None:
                self._sock.sendto(reply, peer)

    def _answer(self, data):
        ident, = struct.unpack_from(">H", data)
        labels, kind, end = _question(data)
        domain = ".".join(labels).lower()
        self.asked.append((domain, kind))
        if domain in self.silent:
            return None
        question = data[12:end]
        if domain in self.failing:
            return struct.pack(">HHHHHH", ident, 0x8182, 1, 0, 0, 0) + question
        if domain not in self.table:
            return struct.pack(">HHHHHH", ident, 0x8183, 1, 0, 0, 0) + question
        hosts = self.table[domain] if kind == MX else []
        records = b"".join(struct.pack(">HHHIH", 0xC00C, MX, IN, 300, 2 + len(_name(host.split(".")))) +
                           struct.pack(">H", 10 * (i + 1)) + _name(host.split(".")) for i, host in enumerate(hosts))
        return struct.pack(">HHHHHH", ident, 0x8180, 1, len(hosts), 0, 0) + question + records
