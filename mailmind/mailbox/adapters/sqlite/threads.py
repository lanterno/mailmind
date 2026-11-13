"""Which conversation each message belongs to: `domain.threading` decides, and this reads what it needs and writes what it says."""
from mailmind.mailbox.adapters.sqlite import bound
from mailmind.mailbox.domain.events import ConversationsMerged
from mailmind.mailbox.domain.threading import Copy, Threader


_THREADING = "pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts"
_SLICE = 500                                     # how many values one IN names


def _copy(r):
    return Copy(r["pk"], r["message_id"], r["gmail_thread"], r["in_reply_to"], r["refs"], r["thread_id"], r["folder"], r["ts"])


def _slices(values):
    values = sorted(values)
    for start in range(0, len(values), _SLICE):
        part = values[start:start + _SLICE]
        yield part, ",".join("?" * len(part))


def _threads_naming(c, account, names):
    """For each of `names`, the ids of the threads holding a copy that names it. CROSS JOIN keeps the planner from
    starting at the account's messages."""
    touching = {}
    for part, marks in _slices(names):
        for r in c.execute(f"""SELECT DISTINCT l.name, m.thread_id FROM thread_links l CROSS JOIN messages m ON m.pk=l.message_pk
                               WHERE l.name IN ({marks}) AND m.account=? AND m.thread_id IS NOT NULL""", [*part, account]):
            touching.setdefault(r["name"], set()).add(r["thread_id"])
    return touching


def _write_links(c, copies):
    """The ids each of these copies names, as it names them now. See migration 42."""
    c.executemany("DELETE FROM thread_links WHERE message_pk=?", [(m.pk,) for m in copies])
    c.executemany("INSERT OR IGNORE INTO thread_links(name, message_pk) VALUES (?,?)",
                  [(name, m.pk) for m in copies for name in m.names()])


def needs_threading(c, pk, in_reply_to, refs):
    """Whether a copy about to hold these references has anything for threading to do: it has no id yet, or they
    aren't the ones it holds. A full rescan stores every copy again, and threading hears of none of them."""
    r = c.execute("SELECT thread_id, in_reply_to, refs FROM messages WHERE pk=?", (pk,)).fetchone()
    return not r or not r["thread_id"] or (r["in_reply_to"] or "", r["refs"] or "") != (in_reply_to, refs)


def thread_stored(c, account, pks=()):
    """Give what a sync stored its thread id: the copies named, and any of the account's with no id yet, since a sync that
    died before this point or `mailmind sync` left them so. It reads what they name and the threads that touches, and
    rewrites only those. A brand-new thread's id comes from all that arrived for it here, which is why it runs once
    per sync and not for each message. In the caller's transaction, which it opens if there's work: what it reads
    decides what it writes, so no other writer may come between."""
    pks = list(pks)
    if not pks and not c.execute("SELECT 1 FROM messages INDEXED BY idx_msg_thread WHERE thread_id IS NULL AND account=? LIMIT 1",
                                 (account,)).fetchone():
        return
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    arrivals = {r["pk"]: _copy(r) for r in c.execute(
        f"SELECT {_THREADING} FROM messages INDEXED BY idx_msg_thread WHERE thread_id IS NULL AND account=?", (account,))}
    for part, marks in _slices(pks):
        arrivals.update((r["pk"], _copy(r)) for r in c.execute(
            f"SELECT {_THREADING} FROM messages WHERE pk IN ({marks}) AND account=?", [*part, account]))
    threader = Threader(account)
    conversations = threader.conversations(
        arrivals.values(), _threads_naming(c, account, {name for m in arrivals.values() for name in m.names()}))
    members = []
    for part, marks in _slices(threader.contested(conversations)):
        members += [_copy(r) for r in c.execute(
            f"SELECT {_THREADING} FROM messages INDEXED BY idx_msg_thread WHERE thread_id IN ({marks}) AND account=?", [*part, account])]
    placed = threader.place(conversations, members)
    c.executemany("UPDATE messages SET thread_id=? WHERE pk=?",
                  [(placed.given[m.pk], m.pk) for m in arrivals.values() if m.thread_id != placed.given[m.pk]])
    _write_links(c, list(arrivals.values()))
    # No survivor is absorbed itself, so these go in any order.
    for absorbed, survivor in placed.merged.items():
        c.execute("UPDATE messages INDEXED BY idx_msg_thread SET thread_id=? WHERE thread_id=? AND account=?",
                  (survivor, absorbed, account))
        bound._merged(c, ConversationsMerged(account, survivor, absorbed))


def recompute_threads(c, account):
    """The repair (`python -m mailmind db rethread`): regroup every message of the account from what each names, and
    write what each names again. No sync comes this way (thread_stored), and nothing has to."""
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    copies = [_copy(r) for r in c.execute(f"SELECT {_THREADING} FROM messages WHERE account=?", (account,))]
    placed = Threader(account).regroup(copies)
    for pk, tid in placed.given.items():
        c.execute("UPDATE messages SET thread_id=? WHERE pk=? AND (thread_id IS NULL OR thread_id<>?)", (tid, pk, tid))
    _write_links(c, copies)
    for absorbed, survivor in placed.merged.items():
        bound._merged(c, ConversationsMerged(account, survivor, absorbed))
