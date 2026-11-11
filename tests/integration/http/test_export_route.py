"""/api/export: a snapshot of the database as a download, read in chunks, and its folder gone afterwards however that went. What it
holds, and the headers, are in tests/golden/http_get/. /api/export/archive: the mail in a standard format, the same way.
"""
import collections
import email
import io
import mailbox
import os
import re
import socket
import threading
import zipfile

import pytest
from anyio import from_thread, to_thread

import problems
import schema_contract
from mailmind.platform.db import export
from waiting import wait_until


@pytest.fixture
def folders(monkeypatch):
    """The folder of each export the test made, with the real snapshot in it, so a test can see it go."""
    made, real = [], export.export_db

    def noting(out):
        made.append(out)
        return real(out)
    monkeypatch.setattr(export, "export_db", noting)
    return made


@pytest.fixture
def big_export(monkeypatch):
    """An export too big for any socket's buffers to take at once. Each folder it made is noted."""
    made = []

    def big(out):
        made.append(out)
        path = os.path.join(out, "mailmind-big.db")
        with open(path, "wb") as f:
            f.truncate(64 * 1024 * 1024)
        return path
    monkeypatch.setattr(export, "export_db", big)
    return made


def opened(app):
    """A client that asks for the export and reads no answer."""
    s = socket.create_connection(("127.0.0.1", app.port), timeout=10)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.sendall(f"GET /api/export HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nSec-Fetch-Site: same-origin\r\n\r\n".encode())
    return s


def test_a_download_holds_the_snapshot_and_its_folder_goes_afterwards(app, folders):
    r = app.get("/api/export")
    assert r.status == 200 and int(r.headers["content-length"]) == len(r.body)
    assert not r.body.startswith(b"SQLite format 3")             # encrypted, as the database is
    assert r.headers["content-type"] == "application/octet-stream"
    assert re.fullmatch(r"attachment; filename=mailmind-\d{8}T\d{6}Z\.db", r.headers["content-disposition"])
    assert len(folders) == 1 and wait_until(lambda: not os.path.exists(folders[0]))


def test_a_failed_snapshot_is_the_fixed_failure_and_leaves_no_folder(app, monkeypatch, capsys):
    made = []

    def boom(out):
        made.append(out)
        raise OSError(13, "hunter2", "hunter2")
    monkeypatch.setattr(export, "export_db", boom)
    r = app.get("/api/export")
    assert (r.status, r.json) == (500, problems.FAILED)
    assert not os.path.exists(made[0])
    said = capsys.readouterr().out
    assert "[http] GET /api/export failed: PermissionError" in said and "hunter2" not in said


def test_a_client_that_hangs_up_midway_leaves_no_folder(app, big_export):
    s = opened(app)
    assert s.recv(4096).startswith(b"HTTP/1.1 200")
    assert os.path.exists(big_export[0])
    s.close()
    assert wait_until(lambda: not os.path.exists(big_export[0]))


def test_a_client_that_hangs_up_during_the_snapshot_leaves_no_folder(app, monkeypatch):
    made, asked, go_on = [], threading.Event(), threading.Event()

    def slow(out):
        made.append(out)
        asked.set()
        go_on.wait(10)
        path = os.path.join(out, "mailmind-slow.db")
        with open(path, "wb") as f:
            f.write(b"x")
        return path
    monkeypatch.setattr(export, "export_db", slow)
    s = opened(app)
    assert asked.wait(5)
    s.close()
    go_on.set()
    assert wait_until(lambda: made and not os.path.exists(made[0]))


def test_a_client_that_reads_slowly_holds_no_thread_of_the_pool(app, big_export, monkeypatch):
    def limiter():
        return from_thread.run_sync(lambda: to_thread.current_default_thread_limiter().borrowed_tokens)
    s = opened(app)
    try:
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        monkeypatch.setattr(schema_contract, "HOLDING", False)
        monkeypatch.setattr(schema_contract.route_for("GET", "/api/stats"), "work", lambda: {"tokens": limiter()})
        # Once the socket is full the response waits on it with no thread, and the one borrowed is the stats' own. A
        # response that held one while it waited would hold it for as long as the client doesn't read: this never.
        assert wait_until(lambda: app.get("/api/stats").json == {"tokens": 1})
    finally:
        s.close()


# ── the mail in a standard format ──────────────────────────────────────────────────────────────────

@pytest.fixture
def archives(monkeypatch):
    """The folder of each archive the test made."""
    made, real = [], export.archive

    def noting(out, form):
        made.append(out)
        return real(out, form)
    monkeypatch.setattr(export, "archive", noting)
    return made


def safely(group):
    return export._safe(group["account"], "acct"), export._safe(group["folder"], "folder")


def test_an_eml_archive_holds_each_message_as_a_file_under_its_account_and_folder(app, archives):
    plan = app.get("/api/export/plan").json
    assert plan["total"] > 0
    r = app.get("/api/export/archive?format=eml")
    assert r.status == 200 and r.headers["content-type"] == "application/zip" and int(r.headers["content-length"]) == len(r.body)
    assert re.fullmatch(r"attachment; filename=mailmind-eml-\d{8}T\d{6}Z\.zip", r.headers["content-disposition"])
    with zipfile.ZipFile(io.BytesIO(r.body)) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert all(name.endswith(".eml") for name in names)
        assert collections.Counter(name.rsplit("/", 1)[0] for name in names) == {"/".join(safely(g)): g["messages"] for g in plan["groups"]}
        said = [email.message_from_bytes(z.read(name)) for name in names]
    assert all(m["From"] and m["Message-ID"] for m in said)
    assert wait_until(lambda: not os.path.exists(archives[0]))


def test_an_mbox_archive_holds_a_file_for_each_folder_that_a_mail_program_can_read(app, archives, tmp_path):
    plan = app.get("/api/export/plan").json
    r = app.get("/api/export/archive?format=mbox")
    assert r.status == 200 and r.headers["content-type"] == "application/zip"
    assert re.fullmatch(r"attachment; filename=mailmind-mbox-\d{8}T\d{6}Z\.zip", r.headers["content-disposition"])
    with zipfile.ZipFile(io.BytesIO(r.body)) as z:
        z.extractall(tmp_path)
        assert len(z.namelist()) == len(plan["groups"])
    for group in plan["groups"]:
        account, folder = safely(group)
        box = mailbox.mbox(tmp_path / account / f"{folder}.mbox", create=False)
        assert len(box) == group["messages"] and all(m["From"] and m["Message-ID"] for m in box)
        box.close()
    assert wait_until(lambda: not os.path.exists(archives[0]))


@pytest.mark.parametrize("query", ["", "?format=", "?format=db", "?format=pst", "?format=EML"])
def test_a_format_we_do_not_offer_is_the_bad_request_and_nothing_is_written(app, archives, query):
    r = app.get("/api/export/archive" + query)
    assert (r.status, r.json) == (400, problems.refused())
    assert archives == []


def test_a_failed_archive_is_the_fixed_failure_and_leaves_no_folder(app, monkeypatch, capsys):
    made = []

    def boom(out, form):
        made.append(out)
        raise OSError(13, "hunter2", "hunter2")
    monkeypatch.setattr(export, "archive", boom)
    r = app.get("/api/export/archive?format=mbox")
    assert (r.status, r.json) == (500, problems.FAILED)
    assert not os.path.exists(made[0])
    said = capsys.readouterr().out
    assert "[http] GET /api/export/archive failed: PermissionError" in said and "hunter2" not in said
