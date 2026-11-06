"""A QuerySpec as SQLite full-text search and SQL.

Injection-safe: FTS column specs are fixed literals; every value is FTS-quoted (embedded " doubled) and
the whole MATCH string is passed as one bound parameter; structured values are bound `?` params.
"""
from mailmind.discovery.domain.query import Kind

# How much a hit counts in each msg_fts column: subject, from_name, from_addr, recipients, snippet,
# body_text. Search and Ask both rank by it: a subject above a sender above the body, and a snippet is
# only the start of the body, so it adds little.
BM25 = "bm25(msg_fts,8.0,4.0,4.0,2.0,1.0,3.0)"

_COLUMNS = {"from": "{from_name from_addr}", "subject": "subject",
            "body": "body_text", "to": "recipients", "cc": "recipients"}   # To and Cc are indexed together
_FLAGS = {"unread": ("seen", 0), "read": ("seen", 1), "flagged": ("flagged", 1),
          "answered": ("answered", 1), "junk": ("is_junk", 1)}
_EXACT = {Kind.BUCKET: ("m.bucket", "IS NOT"), Kind.CATEGORY: ("s.category", "IS NOT"),   # no value isn't excluded
          Kind.FOLDER: ("m.folder", "<>"), Kind.ACCOUNT: ("m.account", "<>")}


def _quoted(value):
    return '"' + value.replace('"', '""') + '"'


def to_sql(spec):
    """-> (match_or_None, where_fragments, params). match is None when the query is purely structured,
    and the caller then runs no MATCH. Mail with no value for a negated field isn't excluded."""
    positive, negative, where, params = [], [], [], []
    for c in spec.clauses:
        if c.kind is Kind.TEXT:
            (negative if c.negated else positive).append(_quoted(c.value))
        elif c.kind is Kind.FIELD:
            (negative if c.negated else positive).append(_COLUMNS[c.field] + " : " + _quoted(c.value))
        elif c.kind is Kind.FLAG:
            column, on = _FLAGS[c.field]
            where.append(f"m.{column} = ?")
            params.append((1 - on) if c.negated else on)
        elif c.kind is Kind.SNOOZED:
            where.append("m.snoozed_until IS " + ("NULL" if c.negated else "NOT NULL"))
        elif c.kind in _EXACT:
            column, unlike = _EXACT[c.kind]
            where.append(f"{column} {unlike if c.negated else '='} ?")
            params.append(c.value)
        elif c.kind is Kind.ATTACHMENT:
            where.append("m.has_attach = ?")
            params.append(0 if c.negated else 1)
        else:                               # a time: a negated one reads as the time itself, as it always has
            where.append("m.ts < ?" if c.kind is Kind.BEFORE else "m.ts >= ?")
            params.append(c.value)
    if positive and negative:
        match = " ".join(positive) + " NOT (" + " OR ".join(negative) + ")"
    elif positive:
        match = " ".join(positive)
    else:
        match = None                        # purely structured
        if negative:                        # FTS5 has no bare NOT: what MATCH finds is left out instead
            where.append("m.pk NOT IN (SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?)")
            params.append(" OR ".join(negative))
    return match, where, params


def visible(where):
    """`where`, leaving out snoozed mail unless the query asks about it. A snoozed message is out of
    sight until it wakes, in a search and in what a command acts on."""
    return where if any("m.snoozed_until" in w for w in where) else where + ["m.snoozed_until IS NULL"]
