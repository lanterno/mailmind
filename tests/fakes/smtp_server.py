"""A scripted SMTP submission server on a loopback port, for tests that drive MailMind's real
smtplib code (compose.adapters.smtp, the outbox).

It speaks what smtplib sends: EHLO/HELO, STARTTLS, AUTH PLAIN and LOGIN, MAIL FROM, RCPT TO,
DATA (dot-stuffed), RSET, NOOP and QUIT. STARTTLS is required before AUTH, so a password never
crosses in the clear, and AUTH before MAIL. The certificate is a throwaway from trustme; the app
trusts any certificate on loopback, as it does for Proton Bridge.

Every message accepted is kept in `messages` (who logged in, the envelope, the bytes), and every
command in `commands`, with AUTH secrets redacted. `on_message`, when set, is called with each
accepted message: the world uses it to file Gmail's own Sent copy, as Gmail does.

Tests script trouble with `fail` (answer a command with an error), `drop` (close the connection
when it arrives) and `drop_after_data` (accept the message, then close before saying so: the
client can't tell whether it was sent).

    with FakeSMTPServer() as smtp:
        smtp.add_user("ada@example.test", "app-password")
        smtp.fail("RCPT", "550 5.1.1 no such user", when=lambda arg: "nobody" in arg)
"""
import base64
import hashlib
import socketserver
import ssl
import threading
from dataclasses import dataclass

import trustme


@dataclass
class Delivery:
    user: str                 # who logged in
    mail_from: str
    rcpts: list
    data: bytes               # as the client sent it, dot-stuffing undone, CRLF line ends


@dataclass
class _Fault:
    command: str              # EHLO, STARTTLS, AUTH, MAIL, RCPT, DATA, or DATA-END (after the dot)
    reply: str | None = None
    drop: bool = False
    times: int | None = 1
    when: object = None       # optional predicate on the command's argument text


class _Session:
    def __init__(self, srv, sock):
        self.srv, self.sock = srv, sock
        self.rfile = sock.makefile("rb")
        self.tls = False
        self.user = None
        self.mail_from = None
        self.rcpts = []

    def send(self, line):
        self.sock.sendall(line.encode() + b"\r\n")

    def readline(self):
        line = self.rfile.readline(65536)
        if not line:
            raise ConnectionError("client went away")
        return line

    def run(self):
        self.send("220 fake-smtp ESMTP ready")
        while True:
            line = self.readline().rstrip(b"\r\n").decode("utf-8", "replace")
            verb, _, arg = line.partition(" ")
            verb = verb.upper()
            self.srv._record(self, verb, "AUTH <redacted>" if verb == "AUTH" else line)
            fault = self.srv._fault_for(verb, arg)
            if fault and fault.drop:
                return
            if fault:
                self.send(fault.reply)
                continue
            handler = getattr(self, "do_" + verb, None)
            if handler is None:
                self.send("502 5.5.2 command not recognized")
                continue
            if handler(arg) == "quit":
                return

    def do_EHLO(self, arg):
        lines = ["fake-smtp", "SIZE 52428800", "8BITMIME", "SMTPUTF8"]
        if not self.tls:
            lines.append("STARTTLS")
        else:
            lines.append("AUTH PLAIN LOGIN")
        for i, text in enumerate(lines):
            self.send(("250 " if i == len(lines) - 1 else "250-") + text)

    def do_HELO(self, arg):
        self.send("250 fake-smtp")

    def do_STARTTLS(self, arg):
        if self.tls:
            self.send("503 5.5.1 TLS already active")
            return
        self.send("220 2.0.0 ready to start TLS")
        self.sock = self.srv.tls_context.wrap_socket(self.sock, server_side=True)
        self.rfile = self.sock.makefile("rb")
        self.tls, self.user = True, None

    def do_AUTH(self, arg):
        if not self.tls:
            self.send("530 5.7.0 must issue a STARTTLS command first")
            return
        mech, _, initial = arg.partition(" ")
        mech = mech.upper()
        try:
            if mech == "PLAIN":
                if not initial:
                    self.send("334 ")
                    initial = self.readline().strip().decode()
                _, user, password = base64.b64decode(initial).decode().split("\0")
            elif mech == "LOGIN":
                self.send("334 VXNlcm5hbWU6")
                user = base64.b64decode(self.readline().strip()).decode()
                self.send("334 UGFzc3dvcmQ6")
                password = base64.b64decode(self.readline().strip()).decode()
            else:
                self.send("504 5.5.4 unrecognized authentication type")
                return
        except (ValueError, UnicodeDecodeError):
            self.send("501 5.5.2 cannot decode response")
            return
        if self.srv.users.get(user) != password:
            self.send("535 5.7.8 authentication credentials invalid")
            return
        self.user = user
        self.send("235 2.7.0 authentication successful")

    def do_MAIL(self, arg):
        if not self.user:
            self.send("530 5.7.0 authentication required")
            return
        self.mail_from, self.rcpts = _addr(arg), []
        self.send("250 2.1.0 ok")

    def do_RCPT(self, arg):
        if self.mail_from is None:
            self.send("503 5.5.1 need MAIL first")
            return
        self.rcpts.append(_addr(arg))
        self.send("250 2.1.5 ok")

    def do_DATA(self, arg):
        if not self.rcpts:
            self.send("503 5.5.1 need RCPT first")
            return
        self.send("354 end data with <CR><LF>.<CR><LF>")
        lines = []
        while True:
            line = self.readline()
            if line in (b".\r\n", b".\n"):
                break
            lines.append(line[1:] if line.startswith(b"..") else line)
        data = b"".join(lines)
        fault = self.srv._fault_for("DATA-END", "")
        if fault and not fault.drop:
            self.send(fault.reply)
            return
        delivery = Delivery(self.user, self.mail_from, list(self.rcpts), data)
        n = self.srv._accept(delivery)
        if fault:                                   # accepted, then gone before the client heard
            return "quit"
        self.send(f"250 2.0.0 ok: queued as {n}")
        self.mail_from, self.rcpts = None, []

    def do_RSET(self, arg):
        self.mail_from, self.rcpts = None, []
        self.send("250 2.0.0 ok")

    def do_NOOP(self, arg):
        self.send("250 2.0.0 ok")

    def do_QUIT(self, arg):
        self.send("221 2.0.0 bye")
        return "quit"


def _addr(arg):
    """The address in "FROM:<a@b> SIZE=12" or "TO:<a@b>"."""
    _, _, rest = arg.partition(":")
    rest = rest.strip()
    return rest[1:rest.index(">")] if rest.startswith("<") and ">" in rest else rest.split(" ")[0]


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        session = _Session(self.server.fake, self.request)
        try:
            session.run()
        except (ConnectionError, OSError, ssl.SSLError):
            pass
        finally:
            try:
                session.sock.close()
            except OSError:
                pass


# How often serve_forever looks up from waiting for a connection: stop() waits for that, and every test that has a world stops one.
POLL = 0.005


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, client_address):
        threading.Thread(target=self.process_request_thread, args=(request, client_address),
                         name="fake-smtp-session", daemon=True).start()


class FakeSMTPServer:
    def __init__(self):
        self.users = {}
        self.messages = []
        self.commands = []
        self.on_message = None
        self.lock = threading.RLock()
        self._faults = []
        self._queued = 0
        self.ca = trustme.CA()
        cert = self.ca.issue_cert("127.0.0.1", "localhost")
        self.tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        cert.configure_cert(self.tls_context)
        der = ssl.PEM_cert_to_DER_cert(cert.cert_chain_pems[0].bytes().decode())
        self.cert_sha256 = hashlib.sha256(der).hexdigest()   # the certificate, as a pin names it
        self._tcp = None
        self.port = None

    def add_user(self, user, password="app-password"):
        self.users[user] = password

    def fail(self, command, reply="451 4.3.0 scripted failure", *, times=1, when=None):
        """Answer the next `times` matching commands with `reply` instead of running them.
        "DATA-END" fails the message after its final dot."""
        self._faults.append(_Fault(command.upper(), reply=reply, times=times, when=when))

    def drop(self, command, *, times=1, when=None):
        """Close the connection when a matching command arrives, without running it."""
        self._faults.append(_Fault(command.upper(), drop=True, times=times, when=when))

    def drop_after_data(self, *, times=1):
        """Accept the next message, then close the connection before the 250 that says so."""
        self._faults.append(_Fault("DATA-END", drop=True, times=times))

    def clear_faults(self):
        self._faults.clear()

    def commands_for(self, command=None):
        return [c for c in self.commands if command is None or c["command"] == command.upper()]

    def _record(self, session, verb, line):
        with self.lock:
            self.commands.append({"user": session.user, "command": verb, "line": line})

    def _fault_for(self, verb, arg):
        with self.lock:
            for f in self._faults:
                if f.command == verb and (f.when is None or f.when(arg)) and (f.times is None or f.times > 0):
                    if f.times is not None:
                        f.times -= 1
                    return f
        return None

    def _accept(self, delivery):
        with self.lock:
            self.messages.append(delivery)
            self._queued += 1
            n = self._queued
        if self.on_message:
            self.on_message(delivery)
        return n

    def start(self):
        self._tcp = _TCP(("127.0.0.1", 0), _Handler)
        self._tcp.fake = self
        self.port = self._tcp.server_address[1]
        threading.Thread(target=self._tcp.serve_forever, kwargs={"poll_interval": POLL},
                         name="fake-smtp", daemon=True).start()
        return self

    def stop(self):
        if self._tcp:
            self._tcp.shutdown()
            self._tcp.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
