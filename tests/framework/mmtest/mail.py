"""Mail a scenario delivers, written as a person would describe it: `mail.from_("lin").to(6).cc(4).subject("Venue")`.

Everyone is made up and on example.test. A name the corpus knows (lin, noor) is that person; any other short name is
someone new, `<name>@example.test`. `to(n)` and `cc(n)` with a number mean that many people, the receiving account first
in To, so a reply all has someone to leave out.
"""
import email.utils
import itertools
import uuid
from dataclasses import dataclass, field, replace
from email.message import EmailMessage

DOMAIN = "example.test"
# The world's accounts, by the short name a scenario uses (fixtures/world.py's ACCOUNTS).
ACCOUNTS = {"ada": "Ada Park", "bea": "Bea Novak", "cy": "Cy Okafor"}
# People the corpus already writes as, so a delivered message's sender is someone the app has seen.
PEOPLE = {"lin": ("Lin Mori", "lin@example.test"), "noor": ("Noor Haddad", "noor@studio.example.test"),
          "mae": ("Mae Lindqvist", "mae@guild.example.test"), "ivo": ("Ivo Brandt", "ivo@harbor.example.test")}
# Made-up people for `to(n)` and `cc(n)`, in turn.
CROWD = ("Mo Reyes", "Pat Ito", "Sam Okafor", "Kit Varga", "Jo Lund", "Ren Abe", "Ola Berg", "Tess Moor", "Uma Rao",
         "Vic Hale", "Wen Li", "Yan Ortiz", "Zed Pike", "Ari Dane", "Bo Kerr")
_ids = itertools.count(1)


def address(who):
    """`who` as an address: someone the corpus knows, a short name on example.test, or an address as it is."""
    if "@" in who:
        return who
    return PEOPLE[who][1] if who in PEOPLE else f"{who}@{DOMAIN}"


def named(who):
    """`who` as a From or To header writes it: "Lin Mori <lin@example.test>"."""
    if "@" in who:
        return who
    name = PEOPLE[who][0] if who in PEOPLE else ACCOUNTS.get(who) or who.capitalize()
    return email.utils.formataddr((name, address(who)))


def _crowd(n, skip=0):
    people = []
    for name in CROWD[skip:skip + n]:
        local = name.lower().replace(" ", ".")
        people.append(email.utils.formataddr((name, f"{local}@{DOMAIN}")))
    if len(people) < n:
        raise ValueError(f"only {len(CROWD)} made-up people: ask for fewer")
    return people


@dataclass(frozen=True)
class Mail:
    """A message to deliver. Each method returns a new one, so a scenario can keep a shape and vary it."""

    sender: str = "lin"
    to_: tuple = ()               # header values, or an int: that many people, the account first
    cc_: tuple = ()
    subject_: str = "A note"
    body_: str = "Just a note."
    date: object = None           # a datetime; the world's clock when None
    in_reply_to: str | None = None
    extra: dict = field(default_factory=dict)

    def from_(self, who):
        return replace(self, sender=who)

    def to(self, *who):
        return replace(self, to_=who)

    def cc(self, *who):
        return replace(self, cc_=who)

    def subject(self, text):
        return replace(self, subject_=text)

    def body(self, text):
        return replace(self, body_=text)

    def at(self, when):
        return replace(self, date=when)

    def replying_to(self, message_id):
        return replace(self, in_reply_to=message_id)

    def header(self, name, value):
        return replace(self, extra={**self.extra, name: value})

    def recipients(self, account):
        """To and Cc as header values, for delivery to `account` (an address)."""
        def people(given, skip, me_first):
            if len(given) == 1 and isinstance(given[0], int):
                n = given[0]
                return ([account] + _crowd(n - 1, skip)) if me_first else _crowd(n, skip)
            return [named(w) for w in given]
        to = people(self.to_, 0, True) if self.to_ else [account]
        taken = len(to) - 1 if self.to_ and isinstance(self.to_[0], int) else 0
        cc = people(self.cc_, taken, False) if self.cc_ else []
        return to, cc

    def build(self, account, now):
        """The message's bytes as it arrives at `account`, dated `now` unless it says otherwise."""
        to, cc = self.recipients(account)
        m = EmailMessage()
        m["From"] = named(self.sender)
        m["To"] = ", ".join(to)
        if cc:
            m["Cc"] = ", ".join(cc)
        m["Subject"] = self.subject_
        m["Date"] = email.utils.format_datetime(self.date or now)
        m["Message-ID"] = f"<mmtest-{next(_ids)}-{uuid.uuid4().hex[:12]}@{DOMAIN}>"
        if self.in_reply_to:
            m["In-Reply-To"] = self.in_reply_to
            m["References"] = self.in_reply_to
        for name, value in self.extra.items():
            m[name] = value
        m.set_content(self.body_)
        return bytes(m)


def from_(who):
    return Mail().from_(who)


def addresses(values):
    """The bare addresses in header values, lowercased."""
    return sorted({a.lower() for _, a in email.utils.getaddresses(list(values)) if a})
