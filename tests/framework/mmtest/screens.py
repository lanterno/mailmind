"""Screens: what a scenario holds, in a person's words. Each method is a step with the phrase a person would use, and
calls only the client interface (`press`, `type`, `click`, `find`, `shown`, `settle`), so a screen serves the page today
and the Mac client when it comes.

    app.inbox.row(subject="Venue").open()
    composer = app.reader.reply_all()
    composer.type("Got it, thanks.")
    composer.send()
"""
import functools
import re

from . import mail as mailing


def phrase(words):
    """A screen method as one step of the trace, named by `words`, formatted with the call's arguments."""
    def wrap(method):
        names = method.__code__.co_varnames[1:method.__code__.co_argcount]

        @functools.wraps(method)
        def step(self, *args, **kwargs):
            given = {**dict(zip(names, args)), **kwargs}
            with getattr(self, "app", self).step(words.format(**given)):
                return method(self, *args, **kwargs)
        step.phrase = words
        return step
    return wrap


class App:
    """The app as a scenario drives it, through one client."""

    def __init__(self, client, world):
        self.client, self.world = client, world
        self.inbox = ListScreen(self)
        self.reader = Reader(self)
        self.composer = Composer(self)
        self.tray = Tray(self)
        self.guide = Guide(self)

    def step(self, words):
        trace = self.client.trace
        return trace.step(words) if trace is not None else _Nothing()

    @phrase("open {path}")
    def open(self, path="/"):
        self.client.open(path)

    def press(self, keys):
        self.client.press(keys)

    def type(self, text):
        self.client.type(text)

    def find(self, role, label=None, within=None, **where):
        return self.client.find(role, label, within=within, **where)

    def shown(self):
        return self.client.shown()

    def truth(self):
        return self.client.truth()

    def relax(self, invariant, why):
        """The oracle leaves `invariant` out after the next step alone, which is about a lag on purpose."""
        self.client.trace.oracle.relax(invariant, why)

    def settle(self):
        self.client.settle()

    def picture(self):
        return self.client.picture()

    @property
    def floats(self):
        """What the floats at the edge say now: Undo's offer, a send on its way."""
        return self.shown().floats

    @property
    def toast(self):
        return self.shown().toast

    @phrase("undo")
    def undo(self):
        self.client.press("z")

    @phrase("compose a new message")
    def compose(self):
        self.client.press("c")
        return self.composer


class Row:
    def __init__(self, screen, element, said):
        self.screen, self.element, self.said = screen, element, said

    @property
    def exists(self):
        return self.element.exists

    @property
    def visible(self):
        return self.element.visible

    @property
    def chosen(self):
        return self.element.exists and self.element.selected

    def open(self):
        with self.screen.app.step(f"open the row {self.said}"):
            self.element.click()
        return self.screen.app.reader


class ListScreen:
    """The message list: a place's rows, under its tabs."""

    def __init__(self, app):
        self.app = app

    @phrase("open {account}'s {folder}")
    def open(self, account="ada", folder="INBOX"):
        self.app.client.open(f"/m/{mailing.address(account)}/{folder}")

    @phrase("show the {name} tab")
    def show_tab(self, name):
        self.app.client.click(self.app.find("tab", re.compile("^" + re.escape(name)), within="list"))

    def tab(self, name):
        return self.app.shown().tab(name)

    def count(self, tab):
        """The number on a tab: "Needs you", "Everything", "Unread"."""
        return self.app.shown().count(tab)

    def row(self, subject=None, sender=None):
        where = {k: v for k, v in (("subject", subject), ("sender", sender)) if v is not None}
        said = " ".join(f"{v!r}" for v in where.values())
        return Row(self, self.app.find("row", within="list", **where), said)

    @property
    def subjects(self):
        return self.app.shown().subjects()

    @property
    def title(self):
        return (self.app.shown().list or {}).get("title")


class Reader:
    """The message open beside the list."""

    def __init__(self, app):
        self.app = app

    def _button(self, label):
        return self.app.find("button", label, within="reader")

    @property
    def subject(self):
        opened = self.app.shown().open
        return opened["subject"] if opened else None

    @phrase("reply")
    def reply(self):
        self.app.client.click(self._button("Reply"))
        return self.app.composer

    @phrase("reply to all")
    def reply_all(self):
        self.app.client.click(self._button("Reply all"))
        return self.app.composer

    @phrase("forward")
    def forward(self):
        self.app.client.click(self._button("Forward"))
        return self.app.composer

    @phrase("archive")
    def archive(self):
        self.app.client.click(self._button("Archive"))


class Composer:
    """A message being written."""

    def __init__(self, app):
        self.app = app

    def _find(self, role, label):
        return self.app.find(role, label, within="composer")

    @property
    def open(self):
        return self.app.shown().composer is not None

    @property
    def now(self):
        """What the composer holds: title, to, cc, subject, body."""
        return self.app.shown().composer

    @property
    def send_button(self):
        return self._find("button", re.compile(r"^Send\b"))

    @phrase("address it to {who}")
    def to(self, who):
        self.app.client.click(self._find("combobox", "To"))
        self.app.client.type(mailing.address(who) + ",")

    @phrase("give it the subject {text!r}")
    def subject(self, text):
        self.app.client.click(self._find("textbox", "Subject"))
        self.app.client.type(text)

    @phrase("type {text!r}")
    def type(self, text):
        self.app.client.type(text)

    @phrase("write {text!r} in the body")
    def write(self, text):
        self.app.client.click(self._find("textbox", "message body"))
        self.app.client.type(text)

    @property
    def asks(self):
        """What it asks before it sends, a line each ("Not sent yet · This reply goes to 10 people…"), or []."""
        now = self.now
        return now.asks if now else []

    @phrase("send it")
    def send(self):
        self.app.client.click(self.send_button)

    @phrase("send it anyway")
    def send_anyway(self):
        self.app.client.click(self._find("button", re.compile("anyway$")))

    @phrase("send it later, {when}")
    def send_later(self, when):
        self.app.client.click(self._find("button", re.compile("^Later")))
        self.app.client.click(self.app.find("button", re.compile(re.escape(when) + "$")))

    @phrase("close the composer")
    def close(self):
        self.app.client.press("escape")


class Tray:
    """The outbox tray: what waits to go, and what can be done about it."""

    def __init__(self, app):
        self.app = app

    @property
    def lines(self):
        return self.app.shown().tray

    @phrase("open the outbox tray")
    def open(self):
        self.app.client.click(self.app.find("button", "Outbox and changes waiting"))

    @phrase("cancel {subject!r} in the tray")
    def cancel(self, subject):
        self.app.client.click(self.app.find("button", "Cancel", within="tray", near=subject))


class Guide:
    """The first-run guide: a card a step at a time."""

    def __init__(self, app):
        self.app = app

    def _dialog(self):
        return next((d for d in self.app.shown().dialogs if "Next" in d["buttons"] or "Done" in d["buttons"]), None)

    @property
    def offered(self):
        return self._dialog() is not None

    @property
    def title(self):
        d = self._dialog()
        return d["heading"] if d else None

    @property
    def buttons(self):
        d = self._dialog()
        return d["buttons"] if d else []

    @phrase("go on to the guide's next step")
    def next(self):
        self.app.client.press("enter")


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
