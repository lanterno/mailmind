"""The mail as an archive, answered over the fictional world with the clock frozen at conftest.NOW: a zip of a .eml file a
message, under its account and folder, or of an mbox file a folder, that any other mail program reads.

Each test records the answer's headers, the files the zip holds and what a mail program reads in each message into its
golden file under tests/golden/http_export/. A message whose whole bytes were never fetched is written from what is
stored of it; the Python app loses its Date as it does (tests/unit/platform/test_export.py states that as a known bug),
so the Date is the one header not recorded.
"""
import email
import io
import mailbox
import zipfile
from email import policy

import pytest


def read(message):
    """What a mail program reads of a message: who sent it, its subject, its threading, what the export said of where
    it was and how it was sorted, and the start of its text."""
    def header(name):
        value = message[name]
        return None if value is None else str(value)
    sender = message["From"]
    body = message.get_body(preferencelist=("plain", "html"))
    return {"from": [[a.display_name, a.addr_spec] for a in sender.addresses] if sender is not None else [],
            **{name.lower(): header(name) for name in ("Subject", "Message-ID", "In-Reply-To", "References",
                                                       "X-MailMind-Account", "X-MailMind-Folder", "X-MailMind-ThreadID",
                                                       "X-MailMind-Bucket")},
            "text": body.get_content()[:200] if body is not None else None}


@pytest.mark.parametrize("form", ["eml", "mbox"])
def test_the_mail_as_an_archive(app, golden, tmp_path, form):
    r = app.get(f"/api/export/archive?format={form}")
    golden.record(f"GET /api/export/archive?format={form}", r, body=False)
    assert r.status == 200 and int(r.headers["content-length"]) == len(r.body)
    with zipfile.ZipFile(io.BytesIO(r.body)) as z:
        assert z.testzip() is None
        names = sorted(z.namelist())
        golden.note("files", names)
        z.extractall(tmp_path)
    total = 0
    for name in names:
        if form == "eml":
            messages = [email.message_from_bytes((tmp_path / name).read_bytes(), policy=policy.default)]
        else:
            box = mailbox.mbox(tmp_path / name, create=False)
            messages = [email.message_from_bytes(box.get_bytes(key), policy=policy.default) for key in box.keys()]
            box.close()
        total += len(messages)
        golden.note(name, [read(m) for m in messages])
    assert total == app.get("/api/export/plan").json["total"]


def test_a_format_the_export_does_not_offer_is_the_bad_request(app, golden):
    for query in ("", "?format=", "?format=db", "?format=pst", "?format=EML"):
        golden.record("GET /api/export/archive" + query, app.get("/api/export/archive" + query))
