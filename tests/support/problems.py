"""A failure as the app answers it, for a test to compare an answer with: a problem document (RFC 9457), application/problem+json.

Each kind of the kernel's has its type, `/problems/<kind>`, and a title of its own. What means none of them is `about:blank`, titled
by its status. `detail` is what a person is told, and left out where the page words it; anything else is a member of its own.
"""
from http import HTTPStatus

CONTENT_TYPE = "application/problem+json"


def _doc(kind, title, status, detail, more):
    doc = {"type": f"/problems/{kind}", "title": title, "status": status}
    return {**doc, **({"detail": detail} if detail else {}), **more}


def refused(detail="bad request", **more):
    return _doc("refused", "Refused", 400, detail, more)


def not_found(detail=None, **more):
    return _doc("not-found", "Not found", 404, detail, more)


def conflict(detail=None, **more):
    return _doc("conflict", "Conflict", 409, detail, more)


def busy(detail=None, **more):
    return _doc("busy", "Busy", 409, detail, {"busy": True, **more})


def locked(detail=None, **more):
    return _doc("locked", "Locked", 423, detail, more)


def unreachable(detail=None, **more):
    return _doc("unreachable", "Unreachable", 502, detail, more)


def unreadable(detail=None, **more):
    return _doc("unreadable", "Unreadable", 422, detail, more)


def ai_off():
    return _doc("ai-off", "AI is off", 409, "AI is off: MailMind has no Claude API key", {"ai_off": True})


def read_only():
    return _doc("read-only", "Read-only", 409, "MailMind is running read-only", {"read_only": True})


def over_limit(spent, limit):
    return _doc("over-limit", "Over the daily AI limit", 402, "today's AI spending has reached the daily limit",
                {"over_limit": True, "spent_micros": spent, "limit_micros": limit})


def blank(status, detail=None, **more):
    """One that means none of the kinds: the perimeter's refusals, a 413, the fixed 500 and a route's own literal."""
    return {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status, **({"detail": detail} if detail else {}), **more}


BAD_REQUEST = refused()
FAILED = blank(500, "request failed")
NO_ROUTE = blank(404, "not found")          # a path of the API that no route takes
