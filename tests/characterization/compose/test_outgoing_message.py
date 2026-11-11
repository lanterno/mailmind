"""What a message going out holds: never the reader's quote fold, a tab in its subject kept, and no inline pictures on a forward."""
import email
import email.policy

import pytest

from fixtures import world as w
from mailmind.bootstrap.compose import book
from mailmind.bootstrap import compose
from stored_rows import A, rows


def test_hwf_a_tab_in_a_subject_is_not_deleted():
    import email
    from mailmind.compose import api as compose
    built = compose.build_message({"account": A, "to": ["lin@example.test"], "subject": "Plans\tfor next week",
                                   "text": "hi"})
    assert email.message_from_bytes(built["raw"])["Subject"] == "Plans for next week"


FOLDABLE = {"from_name": "Lin Mori", "from_addr": "lin@example.test", "date": "Mon, 2 Nov 2026", "text": None,
            "html": "<p>Sounds good.</p><blockquote>On Sun, Ada wrote: lunch?</blockquote>"}


@pytest.mark.parametrize("bodies", ["reply_bodies", "forward_bodies"])
def test_hwf_mail_going_out_carries_no_reader_quote_fold(bodies):
    from mailmind.compose import api as compose
    text, html = getattr(compose, bodies)("Great", FOLDABLE)
    assert "<details" not in html and "quoted text" not in text
    assert "On Sun, Ada wrote: lunch?" in html and "On Sun, Ada wrote: lunch?" in text


def test_hwf_the_reader_still_folds_a_quote():
    from mailmind.content import sanitize
    assert '<details class="mm-quote">' in sanitize.sanitize(FOLDABLE["html"])[0]


def test_hwf_a_quote_stored_with_the_fold_loses_only_the_fold():
    from mailmind.content import sanitize
    folded = sanitize.sanitize(FOLDABLE["html"])[0]
    assert sanitize.without_fold(folded) == sanitize.sanitize(FOLDABLE["html"], fold=False)[0]
    assert sanitize.without_fold(f"<blockquote>{folded}</blockquote>") == \
        f'<blockquote>{sanitize.sanitize(FOLDABLE["html"], fold=False)[0]}</blockquote>'
    theirs = "<details><summary>Their own</summary>Kept</details>"
    assert sanitize.without_fold(theirs) == theirs


def test_hwf_a_draft_saved_with_the_fold_goes_out_without_it(world):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    fold = '<details class="mm-quote"><summary>··· quoted text</summary>'
    did = book.draft_upsert(c, {"id": None, "account": A, "folder": "Drafts", "uid": 7, "message_id": "<d@example.test>",
                                 "uidvalidity": 1, "mode": "reply", "subject": "Re: Lunch",
                                 "created_at": "2026-10-01T09:00:00", "updated_at": "2026-10-01T09:00:00"})
    book.draft_set_resume(c, did, "Great", "\n\nOn Sunday:\n> lunch?\n",
                           f"<div>On Sunday:</div><blockquote>{fold}<blockquote>lunch?</blockquote></details></blockquote>",
                           None, "sha")
    c.commit()
    c.close()
    spec, _, _ = compose.composing.spec({"account": A, "mode": "reply", "to": ["lin@example.test"], "subject": "Re: Lunch",
                                       "text": "Great", "draft_id": did,
                                       "resume": {"tail_text": "\n\nOn Sunday:\n> lunch?\n"}})
    assert "<details" not in spec["html"] and "lunch?" in spec["html"]


def test_hwf_a_forward_leaves_inline_pictures_behind(app, world):
    r = app.post("/api/send", {"account": w.ADA, "mode": "forward", "original_key": world.key("ada-inbox-11-html-cid.eml"),
                               "to": ["noor@studio.example.test"], "subject": "Fwd: The finished quilt",
                               "text": "Look!", "idempotency_key": "k-fwd-cid"}).json
    assert r["ok"]
    [row] = rows("SELECT raw FROM outbox")
    msg = email.message_from_bytes(row["raw"], policy=email.policy.default)
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == []
