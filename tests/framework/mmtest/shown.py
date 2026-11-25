"""`Shown`: what a person sees, as text first (test-architecture.md sections 3 and 8).

The page client reads it from the DOM in one pass (READ below): the regions by their anchors (anchors.toml) or by role and
label, and within them what a person reads, visible only. It is never read from the app's own state. The Mac client
will read the same shape from the accessibility tree.

    page "MailMind" /m/ada%40example.test/INBOX
      rail: Ada · [Inbox 10]* [Sent] [Drafts] … [Screener 6] [Snoozed]
      list "Inbox": Needs you 0 · Everything* 11 · Unread 10
      list: 11 rows, chosen 1
        > Prize Desk — CONGRATULATIONS you have WON … · 9h ago · spam
          Lin Mori — The finished quilt · yesterday · unread
      reader: "CONGRATULATIONS you have WON …" from Prize Desk
      keyboard: list
"""
import difflib
from dataclasses import dataclass, field

ROWS_SHOWN = 12          # rows a text snapshot lists; the rest are counted
WIDTH = 60               # a subject or sentence is cut here

# The page's reading of itself. Regions come by anchor (data-mm) or by role and label; a few parts of a row, the reader
# and the composer that nothing labels are read by the page's own class names, here and nowhere else.
READ = r"""
() => {
  const vis = (el) => {
    if (!el || !el.isConnected) return false;
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  const txt = (el) => (el ? (el.innerText ?? el.textContent ?? "") : "").replace(/\s+/g, " ").trim();
  // Only what the eye reads: words a screen reader alone hears (clipped to a pixel) are left out.
  const seen = (el) => {
    let said = txt(el);
    for (const c of el ? el.querySelectorAll("*") : []) {
      const r = c.getBoundingClientRect();
      if (r.width <= 1 && r.height <= 1 && txt(c)) said = said.replace(txt(c), " ");
    }
    return said.replace(/\s+/g, " ").trim();
  };
  const anchor = (n) => [...document.querySelectorAll(`[data-mm="${n}"]`)].find(vis) || null;
  const labelOf = (el) => {
    const by = el.getAttribute("aria-labelledby");
    if (by) return by.split(/\s+/).map((id) => txt(document.getElementById(id))).join(" ").trim();
    return el.getAttribute("aria-label") || "";
  };
  const out = { title: document.title, path: location.pathname + location.search };

  const rail = document.querySelector('aside[aria-label="Accounts and folders"]');
  if (vis(rail)) {
    const account = rail.querySelector('button[aria-label^="Account: "]');
    out.rail = {
      account: account ? account.getAttribute("aria-label").replace(/^Account: /, "").replace(/\. Switch account$/, "") : "",
      places: [...rail.querySelectorAll("nav a")].filter(vis).map((a) => ({ name: txt(a), current: a.getAttribute("aria-current") === "page" })),
    };
  }

  const list = anchor("list");
  if (list) {
    const heading = list.querySelector("h1");
    const box = list.querySelector('[role="listbox"]');
    const rows = box ? [...box.querySelectorAll('[role="option"]')] : [];
    const row = (r) => {
      const sender = txt(r.querySelector(".from")), when = txt(r.querySelector(".when"));
      let head = txt(r.querySelector(".r1"));
      for (const part of [sender, when]) if (part) head = head.replace(part, " ");
      return {
        sender, when, subject: txt(r.querySelector(".sj")), tags: head.replace(/\s+/g, " ").trim(),
        unread: r.classList.contains("unread"), chosen: r.getAttribute("aria-selected") === "true",
        marked: r.getAttribute("aria-checked") === "true", visible: vis(r),
      };
    };
    let empty = "";
    if (!rows.length) {
      const said = [...list.querySelectorAll('[role="status"], .nores, .empty, p')].filter(vis).map(txt).filter(Boolean);
      empty = said.join(" ");
    }
    out.list = {
      title: txt(heading),
      tabs: [...list.querySelectorAll('[role="tab"]')].filter(vis).map((t) => ({ name: txt(t), selected: t.getAttribute("aria-selected") === "true" })),
      rows: rows.map(row), empty,
    };
  }

  const reader = anchor("reader");
  if (reader) {
    if (reader.getAttribute("aria-label") === "Message") {
      const subj = reader.querySelector(".rd-subj");
      const count = txt(subj && subj.querySelector(".thr-n"));
      out.reader = { kind: "message", subject: txt(subj).replace(count, "").trim(), sender: txt(reader.querySelector(".rd-from .nm")), thread: count };
    } else {
      const brief = reader.querySelector('section[aria-label="Today’s brief"], [role="region"][aria-label="Today’s brief"]');
      const said = brief ? [...reader.querySelectorAll("h1, h2, p")].filter(vis).map(txt).join(" · ") : txt(reader);
      out.reader = { kind: brief ? "brief" : "empty", said };
    }
  }

  out.composers = [...document.querySelectorAll('[data-mm="composer"]')].filter(vis).map((c) => {
    const chips = (field) => [...c.querySelectorAll(`${field} button[aria-label^="Remove "]`)].map((b) => b.getAttribute("aria-label").slice(7));
    const subject = c.querySelector('input[id="cmpSubject"]');
    const body = c.querySelector('textarea[aria-label="message body"]');
    // What it asks before it sends: a group named for it ("Not sent yet"), its lines and its buttons.
    const asks = [...c.querySelectorAll('[role="group"]')].filter((g) => vis(g) && g.querySelector("li")).map((g) =>
      [labelOf(g), ...[...g.querySelectorAll("li")].map(txt), ...[...g.querySelectorAll("button")].filter(vis).map((b) => `[${txt(b)}]`)].join(" · "));
    return { title: txt(c.querySelector("h3")), to: chips("#cmpTo"), cc: chips("#cmpCc"), bcc: chips("#cmpBcc"),
             subject: subject ? subject.value : "", body: body ? body.value : "", asks };
  });

  out.dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
    .filter((d) => vis(d) && !d.querySelector('[data-mm="composer"]'))
    .map((d) => ({ name: labelOf(d), heading: txt(d.querySelector("h1, h2, h3, h4")),
                   buttons: [...d.querySelectorAll("button")].filter(vis).map((b) => b.getAttribute("aria-label") || txt(b)).filter(Boolean) }));
  out.floats = [...document.querySelectorAll('[data-mm="float"]')].filter(vis).map(seen);
  const toast = anchor("toast");
  out.toast = toast ? seen(toast) : "";
  const tray = anchor("tray");
  out.tray = tray ? [...tray.children].filter(vis).map(txt).filter(Boolean) : null;

  const a = document.activeElement;
  const region = (el) => {
    const named = el.closest("[data-mm]");
    if (named && named.dataset.mm !== "float") return named.dataset.mm;
    const dialog = el.closest('[role="dialog"]');
    if (dialog) return `dialog "${labelOf(dialog)}"`;
    if (el.closest('aside[aria-label="Accounts and folders"]')) return "rail";
    if (el.closest("header, [role=banner]")) return "top bar";
    return "page";
  };
  out.focus = a && a !== document.body && a !== document.documentElement
    ? { region: region(a), role: a.getAttribute("role") || a.tagName.toLowerCase(),
        label: a.getAttribute("aria-label") || labelOf(a) || (a.matches("button, a, [role=tab], [role=option]") ? txt(a).slice(0, 40) : ""), visible: vis(a) }
    : null;
  return out;
}
"""


def cut(text, width=WIDTH):
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 1] + "…"


@dataclass
class Row:
    sender: str
    subject: str
    when: str
    tags: str = ""
    unread: bool = False
    chosen: bool = False
    marked: bool = False
    visible: bool = True

    def line(self):
        parts = [f"{self.sender} — {cut(self.subject)}", self.when]
        if self.tags:
            parts.append(self.tags)
        if self.unread:
            parts.append("unread")
        mark = ">" if self.chosen else " "
        return f"{mark}{'x' if self.marked else ' '}" + " · ".join(p for p in parts if p)


@dataclass
class Composer:
    title: str
    to: list
    cc: list
    bcc: list
    subject: str
    body: str
    asks: list = field(default_factory=list)


def _empty():
    """A new list, for Shown's fields: inside Shown, `list` is its field of that name, not the type."""
    return []


@dataclass
class Shown:
    """What a person sees now. `text()` is the snapshot `mm shown` prints; `diff` is what changed from another."""

    title: str = ""
    path: str = ""
    rail: dict | None = None
    list: dict | None = None
    rows: list = field(default_factory=_empty)
    reader: dict | None = None
    composers: list = field(default_factory=_empty)
    dialogs: list = field(default_factory=_empty)
    floats: list = field(default_factory=_empty)
    toast: str = ""
    tray: list | None = None
    focus: dict | None = None

    @classmethod
    def read(cls, raw):
        listed = raw.get("list")
        return cls(title=raw.get("title", ""), path=raw.get("path", ""), rail=raw.get("rail"), list=listed,
                   rows=[Row(**r) for r in (listed or {}).get("rows", [])], reader=raw.get("reader"),
                   composers=[Composer(**c) for c in raw.get("composers", [])], dialogs=raw.get("dialogs", []),
                   floats=raw.get("floats", []), toast=raw.get("toast", ""), tray=raw.get("tray"), focus=raw.get("focus"))

    # ── what a scenario asks of it ──────────────────────────────────────

    @property
    def chosen(self):
        return next((r for r in self.rows if r.chosen), None)

    @property
    def open(self):
        """The message the reader holds, or None."""
        return self.reader if self.reader and self.reader.get("kind") == "message" else None

    @property
    def composer(self):
        return self.composers[0] if self.composers else None

    def tab(self, name):
        """The list's tab whose name starts with `name`: {"name": "Needs you 2", "selected": bool}."""
        for t in (self.list or {}).get("tabs", []):
            if t["name"].startswith(name):
                return t
        return None

    def count(self, tab):
        """The number a list tab shows, or None."""
        t = self.tab(tab)
        tail = t["name"][len(tab):].strip() if t else ""
        return int(tail) if tail.isdigit() else None

    def subjects(self):
        return [r.subject for r in self.rows]

    # ── as text ─────────────────────────────────────────────────────────

    def text(self):
        lines = [f'page "{self.title}" {self.path}']
        if self.rail:
            places = " ".join(f"[{p['name']}]{'*' if p['current'] else ''}" for p in self.rail["places"])
            lines.append(f"  rail: {self.rail['account']} · {places}".rstrip())
        if self.list is not None:
            tabs = " · ".join(_tab(t) for t in self.list.get("tabs", []))
            lines.append(f'  list "{self.list.get("title", "")}"' + (f": {tabs}" if tabs else ""))
            chosen = next((i + 1 for i, r in enumerate(self.rows) if r.chosen), None)
            marked = sum(r.marked for r in self.rows)
            summary = f"  list: {len(self.rows)} row{'' if len(self.rows) == 1 else 's'}"
            summary += f", chosen {chosen}" if chosen else ""
            summary += f", {marked} marked" if marked else ""
            lines.append(summary)
            lines += [f"    {r.line()}" for r in self.rows[:ROWS_SHOWN]]
            if len(self.rows) > ROWS_SHOWN:
                lines.append(f"    … {len(self.rows) - ROWS_SHOWN} more")
            if not self.rows and self.list.get("empty"):
                lines.append(f"    ({cut(self.list['empty'], 100)})")
        if self.reader:
            if self.reader["kind"] == "message":
                thread = f", {self.reader['thread']}" if self.reader.get("thread") else ""
                lines.append(f'  reader: "{cut(self.reader["subject"])}" from {self.reader["sender"]}{thread}')
            else:
                lines.append(f"  reader: {self.reader['kind']}: {cut(self.reader['said'], 100)}")
        for c in self.composers:
            who = ", ".join(c.to) or "nobody"
            copies = f", cc {', '.join(c.cc)}" if c.cc else ""
            lines.append(f'  composer "{c.title}": to {who}{copies}, subject "{cut(c.subject)}"')
            lines.append(f"    body: {cut(c.body, 100)!r}")
            lines += [f"    asks: {cut(a, 140)}" for a in c.asks]
        for d in self.dialogs:
            buttons = " ".join(f"[{b}]" for b in d["buttons"])
            lines.append(f'  dialog "{d["name"]}": {cut(d["heading"])} {buttons}'.rstrip())
        for f in self.floats:
            lines.append(f"  float: {cut(f, 100)}")
        if self.toast:
            lines.append(f"  toast: {cut(self.toast, 100)}")
        if self.tray is not None:
            lines.append("  tray: " + " | ".join(cut(t, 70) for t in self.tray))
        lines.append("  keyboard: " + _focus(self.focus))
        return "\n".join(lines)

    def diff(self, before):
        """The lines that changed since `before`, as `-` and `+` lines; empty when nothing did."""
        if before is None:
            return self.text()
        out = [line for line in difflib.ndiff(before.text().splitlines(), self.text().splitlines())
               if line[:2] in ("- ", "+ ")]
        return "\n".join(out)

    def __str__(self):
        return self.text()


def _tab(t):
    name = t["name"]
    if t["selected"]:
        head, _, count = name.rpartition(" ")
        return f"{head}* {count}" if count.isdigit() and head else f"{name}*"
    return name


def _focus(f):
    if not f:
        return "nowhere (the page itself)"
    what = f["role"] + (f' "{f["label"]}"' if f.get("label") else "")
    hidden = "" if f.get("visible", True) else " (not visible)"
    return f"{f['region']}: {what}{hidden}" if f["region"] not in ("page",) else what + hidden
