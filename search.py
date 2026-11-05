"""Parse a MailMind search query into a safe (FTS MATCH, SQL WHERE, params) triple.

Grammar (tokenized with shlex so "quoted phrases" stay intact):
  from:VALUE subject:VALUE body:VALUE to:VALUE cc:VALUE  -> FTS column filter
  "a phrase"  bareword                            -> FTS term (phrase / word)
  is:unread|read|flagged|answered|junk            -> structured flag
  is:newsletter|receipt|notification              -> messages.bucket shorthand
  is:snoozed                                      -> structured; else snoozed mail isn't found (visible)
  bucket:VALUE  category:VALUE                     -> exact-match lane filters (bound)
  has:attachment                                  -> structured
  in:FOLDER  account:EMAIL                         -> structured exact
  before:YYYY-MM-DD  after:YYYY-MM-DD              -> structured ts bound, at local midnight
  -TOKEN                                           -> negation (FTS NOT / negated clause; mail with
                                                     no value for a negated field isn't excluded)

Injection-safe: FTS column specs are FIXED literals; every user value is FTS-quoted
(embedded " doubled) and the whole MATCH string is passed as ONE bound parameter;
structured values are bound `?` params. The parser is TOTAL — no input can raise.
"""
import shlex
from datetime import datetime

_FTS_FIELDS = {"from": "{from_name from_addr}", "subject": "subject",
               "body": "body_text", "to": "recipients", "cc": "recipients"}   # To/Cc are indexed
_FLAG = {"unread": ("seen", 0), "read": ("seen", 1), "flagged": ("flagged", 1),
         "answered": ("answered", 1), "junk": ("is_junk", 1)}
# Exact-match structured filters (bound params, never interpolated). category lives in the
# summaries join (s.category: action-needed/personal/work-job/finance/calendar/info/other),
# bucket on the message row (m.bucket) — both fixed enums.
_BUCKET_ALIAS = {                                  # shorthand -> the real mailclass bucket string
    "newsletter": "newsletters/marketing", "newsletters": "newsletters/marketing", "marketing": "newsletters/marketing",
    "receipt": "receipts/transactional", "receipts": "receipts/transactional", "transactional": "receipts/transactional",
    "notification": "automated-notifications", "notifications": "automated-notifications", "automated": "automated-notifications",
    "spam": "spam/scam", "scam": "spam/scam", "blocked": "blocked",
    "personal": "personal/protected", "protected": "personal/protected", "other": "other/personal-ish",
}


def _q(v):
    return '"' + v.replace('"', '""') + '"'


def _ts(v):
    """A date or time as seconds. With no zone it means this machine's, as the person typing it does:
    before:2026-11-02 ends at that midnight here, not at UTC's."""
    try:
        return int(datetime.fromisoformat(v).timestamp())
    except Exception:
        return None


def visible(where):
    """`where`, leaving out snoozed mail unless the query asks about it. A snoozed message is out of
    sight until it wakes, in a search and in what a command acts on."""
    return where if any("m.snoozed_until" in w for w in where) else where + ["m.snoozed_until IS NULL"]


def build(q):
    """-> (match_or_None, where_fragments[list], params[list]).
    match is None when the query is purely structured (caller then runs no MATCH)."""
    q = (q or "").strip()[:512]
    try:
        toks = shlex.split(q)
    except ValueError:                      # unbalanced quote etc.
        toks = q.split()
    pos, neg, where, params = [], [], [], []
    for t in toks:
        neg_t = t.startswith("-") and len(t) > 1
        if neg_t:
            t = t[1:]
        name, sep, val = t.partition(":")
        lname = name.lower()
        if sep and lname in _FTS_FIELDS and val:
            (neg if neg_t else pos).append(_FTS_FIELDS[lname] + " : " + _q(val))
        elif sep and lname == "is" and val.lower() in _FLAG:
            col, on = _FLAG[val.lower()]
            where.append(f"m.{col} = ?")
            params.append((1 - on) if neg_t else on)
        elif sep and lname == "is" and val.lower() == "snoozed":
            where.append("m.snoozed_until IS " + ("NULL" if neg_t else "NOT NULL"))
        elif sep and lname == "is" and val.lower() in _BUCKET_ALIAS:   # is:newsletter/receipt/notification
            where.append("m.bucket " + ("IS NOT" if neg_t else "=") + " ?")
            params.append(_BUCKET_ALIAS[val.lower()])
        elif sep and lname == "bucket" and val:
            where.append("m.bucket " + ("IS NOT" if neg_t else "=") + " ?")
            params.append(_BUCKET_ALIAS.get(val.lower(), val.lower()))
        elif sep and lname == "category" and val:                      # from the summaries join
            where.append("s.category " + ("IS NOT" if neg_t else "=") + " ?")
            params.append(val.lower())
        elif sep and lname == "has" and val.lower() == "attachment":
            where.append("m.has_attach = ?")
            params.append(0 if neg_t else 1)
        elif sep and lname == "in" and val:
            where.append("m.folder " + ("<>" if neg_t else "=") + " ?")
            params.append(val)
        elif sep and lname == "account" and val:
            where.append("m.account " + ("<>" if neg_t else "=") + " ?")
            params.append(val)
        elif sep and lname in ("before", "after") and val:
            ts = _ts(val)
            if ts is not None:
                where.append("m.ts < ?" if lname == "before" else "m.ts >= ?")
                params.append(ts)
        elif t.strip():
            (neg if neg_t else pos).append(_q(t))
    if pos and neg:
        match = " ".join(pos) + " NOT (" + " OR ".join(neg) + ")"
    elif pos:
        match = " ".join(pos)
    else:
        match = None                        # pure-structured
        if neg:                             # FTS5 has no bare NOT: what MATCH finds is left out instead
            where.append("m.pk NOT IN (SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?)")
            params.append(" OR ".join(neg))
    return match, where, params
