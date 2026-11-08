"""Whether a query can be judged of mail as it arrives, which a filter's must be. A message arriving has its headers, what the
classifier made of them and, in the Inbox, the start of its text. It has no body stored yet, no attachments read and no AI verdict.
Words that need those would quietly never match, so a filter is told so instead, in words for the person making it.
"""
from mailmind.discovery.domain.query import MAX_CHARS, Kind, QuerySpec

INBOX = "INBOX"
_UNKNOWN_YET = {
    Kind.ATTACHMENT: "A filter reads mail as it arrives, before its attachments are known, so it can't use has:attachment.",
    Kind.CATEGORY: "A filter reads mail as it arrives, before the AI has read it, so it can't use category:.",
    Kind.SNOOZED: "New mail isn't snoozed, so a filter can't use is:snoozed.",
    Kind.BEFORE: "A filter reads mail as it arrives, so a date says nothing of it. Take out before:.",
    Kind.AFTER: "A filter reads mail as it arrives, so a date says nothing of it. Take out after:.",
}
_NEEDS_SOMETHING = "A filter needs something to look for: a sender, a subject, words, or a kind of mail."
_LOOKS_FOR = frozenset({Kind.TEXT, Kind.FIELD, Kind.BUCKET})


def refusal(text: str) -> str | None:
    """Why this search can't be a filter, or None where it can."""
    if len(text) > MAX_CHARS:
        return "That's too long for a filter."
    spec = QuerySpec.parse(text)
    for c in spec.clauses:
        if c.kind in _UNKNOWN_YET:
            return _UNKNOWN_YET[c.kind]
        if c.kind is Kind.FIELD and c.field == "body":
            return "A filter reads mail as it arrives, before its body is stored, so it can't use body:."
        if c.kind is Kind.FOLDER and not c.negated and c.value != INBOX:
            return f"Filters read mail arriving in the Inbox, so the only folder one can name is in:{INBOX}."
    return None if any(c.kind in _LOOKS_FOR and not c.negated for c in spec.clauses) else _NEEDS_SOMETHING
