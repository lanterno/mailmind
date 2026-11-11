"""A draft is local first (decision D5). A save lands in the journal and answers at once; the server's copy follows it on
close, on an explicit save and at most once a minute while someone types, and a server that's away costs nothing but
waiting. The journal holds one row a draft, the newest save, and the copy a try may have left never stays behind."""

from fixtures import world as w
from mailmind.bootstrap import compose


def new(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order", "text": "Two of each.",
            "idempotency_key": "cmp-1", **kw}


def save(app, **kw):
    r = app.post("/api/draft", new(**kw))
    assert r.status == 200 and r.json["ok"], r.json
    return r.json


def test_discarding_a_draft_with_no_row_answers_as_gone(data_dir):
    assert compose.drafts.save({"delete": True, "draft_id": 12345}) == {"ok": True, "deleted": True}
