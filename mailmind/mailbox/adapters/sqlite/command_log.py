"""The command log's rows: what `mailmind.mailbox.application.commands` keeps of each change to the mail on a server. Its QUEUE moves them through their states."""
import json
import sqlite3
from datetime import datetime, timezone

from mailmind.mailbox.adapters.sqlite.flags import set_local_flags
from mailmind.mailbox.adapters.sqlite.sync_state import get_uidvalidity
from mailmind.mailbox.domain.command import idempotency_key
from mailmind.mailbox.domain.flags import as_columns, held_now


def flag_command(c, row, flags, mutation_id, when):
    """A flag change on message `row`, for the server, shown on the row at once, in the caller's transaction. The
    command keeps the flags it replaced, which a change the server refuses, or one given up on, puts back. Returns
    (id, created): created is False if that id was seen already."""
    acct, mid = row["account"], (row["message_id"] or "").strip()
    params = {"pk": row["pk"], "uid": row["uid"], "epoch": get_uidvalidity(c, acct, row["folder"]), "flags": flags,
              "was": held_now(row, flags)}
    id_, created = command_insert(c, {"account": acct, "idempotency_key": idempotency_key("flag", acct, mid, row["pk"], mutation_id),
                                      "kind": "flag", "message_id": mid, "src_folder": row["folder"],
                                      "dest": None, "params": json.dumps(params),
                                      "next_attempt_at": 0, "created_at": when})
    if created:
        set_local_flags(c, row["pk"], **as_columns(flags))
    return id_, created


def queue_flags(c, row, flags, mutation_id, when):
    """A flag change for the queue's worker alone, as what another context asks for on its own is. False if that id
    was seen already."""
    return flag_command(c, row, flags, mutation_id, when)[1]


def command_insert(c, row):
    """Insert a queued mutation; (id, created) — created=False on idempotency_key dupe. Race-safe."""
    existing = c.execute("SELECT id FROM mail_commands WHERE idempotency_key=?",
                         (row["idempotency_key"],)).fetchone()
    if existing:
        return existing["id"], False
    try:
        cur = c.execute(
            """INSERT INTO mail_commands(account,idempotency_key,kind,message_id,src_folder,dest,
                 params,state,next_attempt_at,created_at)
               VALUES(:account,:idempotency_key,:kind,:message_id,:src_folder,:dest,
                 :params,'queued',:next_attempt_at,:created_at) RETURNING id""", row)
        return cur.fetchone()["id"], True
    except sqlite3.IntegrityError:
        r = c.execute("SELECT id FROM mail_commands WHERE idempotency_key=?",
                      (row["idempotency_key"],)).fetchone()
        return (r["id"] if r else None), False


def command_get(c, mid):
    return c.execute("SELECT * FROM mail_commands WHERE id=?", (mid,)).fetchone()


def command_mark_begun(c, mid):
    """A try at it may reach the server from here on: the next one asks the server before it repeats anything."""
    c.execute("UPDATE mail_commands SET begun=1 WHERE id=?", (mid,))


def command_never_begun(c, mid):
    c.execute("UPDATE mail_commands SET begun=0 WHERE id=?", (mid,))


def command_record(c, mid, dest, outcome):
    """What the server's answer left for a reversal to know: where the message went, and what else."""
    c.execute("UPDATE mail_commands SET dest=COALESCE(?, dest), outcome=? WHERE id=?", (dest, outcome, mid))


def command_set_params(c, mid, params):
    c.execute("UPDATE mail_commands SET params=? WHERE id=?", (params, mid))


def command_open(c, limit):
    """The changes that haven't reached a server, newest first: waiting, being tried or given up."""
    return c.execute("SELECT * FROM mail_commands WHERE applied=0 AND state IN ('queued', 'running', 'error') "
                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def command_open_counts(c):
    """{state: how many} of the changes that haven't reached a server."""
    return {r["state"]: r["n"] for r in c.execute(
        "SELECT state, COUNT(*) n FROM mail_commands WHERE applied=0 "
        "AND state IN ('queued', 'running', 'error') GROUP BY state")}


def flag_changes_waiting(c, account):
    """The Message-IDs with a flag change still on its way to the account's server."""
    return {r["message_id"] for r in c.execute(
        "SELECT message_id FROM mail_commands WHERE account=? AND kind='flag' AND applied=0 "
        "AND state IN ('queued', 'running') AND message_id != ''", (account,))}


def command_forget_finished(c, now_ts, days=30):
    """A queue row finished `days` ago goes: nothing resubmits or undoes it after that. A row still
    open, or in `error` waiting for a person, stays."""
    cutoff = datetime.fromtimestamp(now_ts - days * 86400, timezone.utc).isoformat()
    return c.execute("DELETE FROM mail_commands WHERE state='done' AND done_at < ?", (cutoff,)).rowcount


def command_ran_under(c, mutation_id):
    """Whether any command was accepted under this mutation id."""
    return c.execute("SELECT 1 FROM mail_commands WHERE idempotency_key GLOB ? LIMIT 1", (f"*:{mutation_id}",)).fetchone() is not None


def archives_undone(c):
    """Each message whose archive an Undo took back, as (account, Message-ID), for as long as the log keeps it: that message
    stays where a person put it."""
    return {(r["account"], r["message_id"]) for r in c.execute(
        "SELECT account, message_id FROM mail_commands WHERE kind='archive' AND json_extract(params, '$.undone') IS NOT NULL")}


def command_mark_reversed(c, mid):
    """An Undo took this move back: it was asked and it happened, so its row stays as it is but for saying so."""
    c.execute("UPDATE mail_commands SET params=json_set(COALESCE(params, '{}'), '$.undone', 1) WHERE id=?", (mid,))


def command_latest_move(c, account, message_id):
    """The newest move of a message, in any state: what an undo entry written before entries named their command
    goes by, to decide between a cancel and a counter-move and to find where the message went."""
    return c.execute("SELECT * FROM mail_commands WHERE account=? AND message_id=? AND kind!='flag' "
                     "ORDER BY id DESC LIMIT 1", (account, message_id)).fetchone()
