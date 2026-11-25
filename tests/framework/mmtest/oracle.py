"""The oracle: after every step, what a person sees (`Shown`) is held against what the core holds (`Truth`), read at the
same settled mark, by invariants sharp enough never to need relaxing (test-architecture.md section 8).

`Truth` is read from the world's database, read only, by one adapter (`read`): the drafts the core keeps, the sends
in its outbox, what the fake SMTP server took, and the place the page stands on with the rows its folder holds. Either
client's world has that database, so the oracle serves both.

The three invariants:

- **The keyboard is somewhere.** Something visible has it. On the page, the page itself having it counts, since the
  page hears its keys document-wide (`keys_reach_the_page`); in the Mac app, a window where nothing has the keyboard
  drops a person's keys.
- **Typed words are kept.** Each run of words typed into a composer's body is in a composer on screen, a draft, a
  send in the outbox, or a message sent, unless the scenario discarded it (`Trace.discarded`).
- **The open message is where it says.** A message the reader holds is one of its list's rows as the core holds them,
  or the reader says where it is. Checked where the list is a folder's; other lists join when their views move into
  the core.

An invariant can be relaxed for one step that is about a lag on purpose (`relax`), never for a whole test.
"""
import json
import os
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser

PLACE = re.compile(r"^/p/([^/?#]+)")


class Broken(AssertionError):
    """An invariant didn't hold after a step: it says which, both readings, and why."""


@dataclass
class Truth:
    """What the core holds at the settled mark."""

    drafts: list = field(default_factory=list)          # each draft's own words
    outbox: list = field(default_factory=list)          # each send's plain text, queued or gone
    sent: list = field(default_factory=list)            # what the fake SMTP server took, as plain text
    place: dict | None = None                           # the route the page's place stands for
    place_rows: set | None = None                       # the message keys its folder holds; None when it isn't one

    @property
    def opened_key(self):
        opened = (self.place or {}).get("opened") or None
        return opened.get("key") if opened else None

    def text(self):
        lines = [f"truth: {len(self.drafts)} draft(s), {len(self.outbox)} in the outbox, {len(self.sent)} sent"]
        if self.place is not None:
            lines.append(f"  place: {json.dumps(self.place, sort_keys=True)}")
        if self.place_rows is not None:
            lines.append(f"  its folder holds {len(self.place_rows)} message(s)")
        for kind, texts in (("draft", self.drafts), ("outbox", self.outbox), ("sent", self.sent)):
            lines += [f"  {kind}: {_cut(t)!r}" for t in texts]
        return "\n".join(lines)

    def __str__(self):
        return self.text()


def read(world, shown):
    """`Truth` at this moment, from the world's database (read only) and its fakes."""
    from mailmind.platform.db import database
    sent = [_plain(s.data) for s in world.smtp.sent]
    c = database.keyed(os.path.join(world.home, "mailmind.db"), read_only=True)
    try:
        drafts = [r[0] or "" for r in c.execute("SELECT own_text FROM drafts")]
        drafts += [r[0] or "" for r in c.execute("SELECT own_text FROM draft_journal WHERE discard = 0")]
        outbox = [_plain(r[0]) for r in c.execute("SELECT raw FROM outbox")]
        place = rows = None
        found = PLACE.match((shown.path or "") if shown is not None else "")
        if found:
            row = c.execute("SELECT route FROM places WHERE id = ?", (found.group(1),)).fetchone()
            place = json.loads(row[0]) if row else None
        source = (place or {}).get("source") or {}
        if source.get("kind") == "folder":
            rows = {r[0] for r in c.execute(
                "SELECT message_key FROM messages WHERE account = ? AND folder = ? AND pending_removal IS NULL",
                (source["account"], source["folder"]))}
    finally:
        c.close()
    return Truth(drafts=drafts, outbox=outbox, sent=sent, place=place, place_rows=rows)


# ── the invariants ──────────────────────────────────────────────────────

INVARIANTS = []


def invariant(check):
    INVARIANTS.append(check)
    return check


@invariant
def the_keyboard_is_somewhere(shown, truth, history, client):
    focus = shown.focus
    if focus is None:
        if not getattr(client, "keys_reach_the_page", False):
            return "nothing has the keyboard: a person's keys go nowhere"
        return None
    if not focus.get("visible", True):
        return f"the keyboard is in something no one can see: {focus}"
    return None


@invariant
def typed_words_are_kept(shown, truth, history, client):
    on_screen = [_flat(c.body) for c in shown.composers]
    kept = on_screen + [_flat(t) for t in truth.drafts + truth.outbox + truth.sent]
    lost = [words for words in history.typed_into_bodies() if _flat(words) not in "\n".join(kept)]
    return f"typed words are nowhere: {lost}" if lost else None


@invariant
def the_open_message_is_where_it_says(shown, truth, history, client):
    if shown.open is None or truth.place_rows is None:
        return None
    key = shown.open.get("key") or truth.opened_key
    if key is None or key in truth.place_rows or shown.open.get("says_where"):
        return None
    return f"the reader holds a message its list doesn't (key {key}, not among the folder's {len(truth.place_rows)})"


# ── run after every step ────────────────────────────────────────────────

class Oracle:
    """Runs every invariant after a step of `client`'s, at the settled mark the step ended on."""

    def __init__(self, world, client, history):
        self.world, self.client, self.history = world, client, history
        self._relaxed = {}                  # an invariant's name: why, for the next step only

    def relax(self, name, why):
        """`name` isn't held after the next step, which is about a lag on purpose: `why` says which."""
        if name not in {check.__name__ for check in INVARIANTS}:
            raise KeyError(f"no invariant {name!r}")
        if not why:
            raise ValueError("say why the step may break it")
        self._relaxed[name] = why

    def check(self):
        """Every invariant after a step; raises Broken with both readings when one fails."""
        relaxed, self._relaxed = self._relaxed, {}
        if not self.client.showing():
            return
        shown = self.client.shown()
        truth = read(self.world, shown)
        broken = []
        for check in INVARIANTS:
            if check.__name__ in relaxed:
                continue
            said = check(shown, truth, self.history, self.client)
            if said:
                broken.append(f"{check.__name__}: {said}")
        if broken:
            raise Broken("\n".join(broken) + f"\n\nshown:\n{shown.text()}\n\n{truth.text()}")


def _plain(raw):
    """The plain text a message carries, with its line ends as \\n."""
    message = BytesParser(policy=policy.default).parsebytes(bytes(raw))
    part = message.get_body(preferencelist=("plain",))
    return part.get_content().replace("\r\n", "\n") if part is not None else ""


def _flat(text):
    """Words as a comparison reads them: the whitespace between them is any whitespace."""
    return " ".join(str(text or "").split())


def _cut(text, width=60):
    text = _flat(text)
    return text if len(text) <= width else text[:width - 1] + "…"
