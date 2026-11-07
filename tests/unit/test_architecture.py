"""The dependency rules the import graph can't hold: what's asked of the standard library, the Assistant's ports, who
writes which table, and who still reads a stored pk. import-linter's contracts in pyproject.toml hold the rest."""
import ast
import pathlib
import re
from collections import Counter

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "mailmind"
CONTEXTS = ("accounts", "assistant", "attention", "calendar", "compose", "curation", "discovery", "mailbox")

# Who owns each table: only its context writes it.
OWNER = {
    "accounts": (),
    "assistant": ("ai_batch_items", "ai_batches", "ai_usage", "assessments", "briefs", "deltas", "draft_ahead", "reviews", "summaries",
                  "thread_summaries"),
    "attention": ("followups", "muted", "snoozes"),
    "calendar": (),
    "compose": ("drafts", "draft_journal", "outbox"),
    "curation": ("sender_unsub", "unsub_log"),
    "discovery": ("msg_fts",),
    "mailbox": ("attachments", "bodies", "message_identity", "message_recipients", "messages", "mail_commands", "sync_state", "thread_links"),
    "platform": ("undo_entries",),
}

# Where another context still writes a table, each for a reason this list names. It may only shrink.
CROSSINGS = {
    ("assessments", "mailbox"),     # a copy that arrives takes what was said of its message, in the one transaction
    ("summaries", "mailbox"),       # likewise; and a message that goes takes its verdict with it
    ("snoozes", "mailbox"),         # a copy filed elsewhere leaves its snooze where it was
    ("messages", "attention"),      # a snooze writes messages.snoozed_until
    ("msg_fts", "platform"),        # the upgrade check asks the index to check itself, on a copy
}

# The columns that name a message by pk, which can come to name another (outbox.in_reply_to_pk, drafts.in_reply_to_pk,
# draft_ahead.src_pk, followups.anchor_pk). They are still written, for a rollback, and everything goes by what outlasts
# a row instead: `orig_*`, a draft's own In-Reply-To, the thread or the Message-ID.
PK_COLUMNS = ("in_reply_to_pk", "src_pk", "anchor_pk")

# Where one is still read, and how many times. It may only shrink.
PK_READS = {
    # outbox_item.pk_of_original: outbox.in_reply_to_pk, for a row with no Message-ID to go by. One queued before
    # migration 27 has no `orig_*` at all, and a reply to a message with no Message-ID has nothing else to name it by,
    # since the outbox keeps no message key. Outbox._original_pk checks the second against the account it names.
    "compose/domain/outbox_item.py": 1,
}

# What holds `in_reply_to_pk` as a request said it, a moment old and no stored column: the page's composer sends it.
PK_REQUESTS = {"body", "meta"}

# What a domain may never ask the standard library for: it holds no I/O.
IO = {"sqlite3", "smtplib", "imaplib", "socket", "ssl", "http", "subprocess", "urllib.request"}


def modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" not in path.parts:
            yield path


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def rel(path):
    return path.relative_to(PACKAGE).as_posix()


def test_a_domain_holds_no_io():
    found = {(rel(p), name) for p in modules() if "domain" in p.parts for name in imports(p)
             if name in IO or name.split(".")[0] in IO}
    assert found == set()


def test_only_composes_adapter_speaks_smtp():
    assert {rel(p) for p in modules() if "smtplib" in set(imports(p))} == {"compose/adapters/smtp.py"}


def test_outbound_http_lives_in_the_ai_adapter_and_safe_http():
    calls = re.compile(r"\b(urlopen|HTTPSConnection|HTTPConnection)\(")
    assert {rel(p) for p in modules() if calls.search(p.read_text(encoding="utf-8"))} == {
        "assistant/adapters/urllib_http.py", "curation/adapters/safe_http.py"}


def test_the_assistants_ports_only_read():
    """A plan's confirm runs through the mail's own contexts, so nothing the Assistant is handed can change mail."""
    from mailmind.bootstrap import assistant
    allowed = {"_Mail": {"fetch_body",                                 # fills a local cache, and writes to no server
                         "pk_now", "accounts", "get_message", "any_copy", "for_asking", "brief_rows", "delta_rows",
                         "sent_since", "received_since", "received_by_bucket", "needs_you_rows", "awaited_threads",
                         "replylater_due", "senders_cleaned", "draft_candidates", "thread_stamps", "message_stamp",
                         "thread_anchor_pk", "conversation_rows", "message_to_reply_to"},
               "_Search": {"parse", "reach", "sweeps_whole_mailbox"}}
    for name, methods in allowed.items():
        declared = {m for m in vars(getattr(assistant, name)) if not m.startswith("__")}
        assert declared == methods, name


def test_no_use_case_holds_sql():
    """What one asks of a table goes through a port, to an adapter: no statement, no connection run by hand, no sqlite3.
    A docstring doesn't count: a Mailbox's may well speak of IMAP's SELECT."""
    runs = re.compile(r"\.execute(many|script)?\(")
    statement = re.compile(r"\b(SELECT|INSERT INTO|DELETE FROM|BEGIN IMMEDIATE)\b|\bUPDATE \w+ SET\b")
    found = set()
    for path in sorted(PACKAGE.glob("*/application/*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        prose = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                 if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
        literals = (n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in prose)
        if runs.search(text) or "sqlite3" in set(imports(path)) or any(statement.search(s) for s in literals):
            found.add(rel(path))
    assert found == set()


def test_no_use_case_runs_a_connection_by_hand():
    """A use case says what it reads and what it writes (`self._uow.read()`, `.write()`), and the unit of work opens each
    connection, takes the lock up front, commits and closes. Handing `uow.connect` to a DurableQueue isn't a call: the
    queue runs its own short transactions, in platform."""
    by_hand = {"conn", "connect", "_connect", "commit", "rollback", "close"}
    found = {(rel(path), node.func.attr) for path in sorted(PACKAGE.glob("*/application/*.py"))
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in by_hand}
    assert found == set()


def writers():
    out = {}
    pattern = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)")
    for path in modules():
        place = path.relative_to(PACKAGE).parts[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for table in pattern.findall(node.value):
                    if table not in ("set", "of", "the", "a"):
                        out.setdefault(table, set()).add(place)
    return out


def test_only_the_owner_writes_a_table():
    owner = {table: ctx for ctx, tables in OWNER.items() for table in tables}
    written = writers()
    assert set(written) <= set(owner), "a table with no owner here"
    crossings = {(table, place) for table, places in written.items() for place in places if place != owner[table]}
    assert crossings == CROSSINGS


def pk_columns_sql_reads(literal):
    """The columns of PK_COLUMNS a string of SQL reads: those still named once what it writes is taken out, which is the
    column list of an INSERT, a named parameter, `excluded.` and the targets of a SET."""
    sql = re.sub(r"\bINTO\s+\w+\s*\([^)]*\)", "INTO", literal, flags=re.I)
    sql = re.sub(r":\w+|\bexcluded\.\w+", "", sql, flags=re.I)
    sql = re.sub(r"\bSET\b.*?(?=\bWHERE\b|\Z)", lambda m: re.sub(r"\b\w+\s*=", "=", m.group()), sql, flags=re.I | re.S)
    return re.findall(r"\b(?:" + "|".join(PK_COLUMNS) + r")\b", sql)


def pk_column_is_read_by_name(node):
    """Is this `row["src_pk"]` or `row.get("src_pk")`, on a stored row? A `SELECT *` brings the column along, and this is
    how anyone would then read it."""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        holder, key = node.value, node.slice
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
        holder, key = node.func.value, node.args[0]
    else:
        return False
    return (isinstance(key, ast.Constant) and key.value in PK_COLUMNS
            and not (isinstance(holder, ast.Name) and holder.id in PK_REQUESTS))


def test_a_stored_pk_is_read_only_where_nothing_else_names_the_message():
    found = Counter()
    for path in modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prose = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                 if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            found[rel(path)] += pk_column_is_read_by_name(node)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in prose | set(PK_COLUMNS):
                found[rel(path)] += len(pk_columns_sql_reads(node.value))
    assert +found == PK_READS


@pytest.mark.parametrize("sql", [
    "SELECT in_reply_to_pk FROM outbox WHERE id=?",
    "SELECT o.in_reply_to_pk, o.id FROM outbox o",
    "SELECT 1 FROM followups WHERE anchor_pk=?",
    "UPDATE drafts SET subject=? WHERE in_reply_to_pk=?",
    "UPDATE draft_ahead SET fp=src_pk WHERE account=?",
    "INSERT INTO followups(account, anchor_pk) SELECT account, anchor_pk FROM followups",
    "SELECT m.* FROM messages m JOIN followups f ON f.anchor_pk=m.pk",
])
def test_reading_a_stored_pk_in_sql_is_seen(sql):
    assert pk_columns_sql_reads(sql)


@pytest.mark.parametrize("sql", [
    "INSERT INTO outbox(account,in_reply_to_pk) VALUES(:account,:in_reply_to_pk)",
    "INSERT OR IGNORE INTO followups(account,anchor_pk,due_ts) SELECT m.account, m.pk, ? FROM messages m WHERE m.pk=?",
    "UPDATE drafts SET mode=?, in_reply_to_pk=?, subject=? WHERE id=?",
    "UPDATE drafts SET in_reply_to_pk=:in_reply_to_pk, mode=:mode WHERE id=:id",
    "INSERT INTO draft_ahead(account,src_pk) VALUES(?,?) ON CONFLICT(account) DO UPDATE SET src_pk=excluded.src_pk, fp=excluded.fp",
    "SELECT pk FROM messages WHERE account=? AND thread_anchor_pk=?",
])
def test_writing_a_stored_pk_in_sql_is_no_read(sql):
    assert not pk_columns_sql_reads(sql)


@pytest.mark.parametrize("code, read", [
    ('row["in_reply_to_pk"]', True),
    ('row.get("src_pk")', True),
    ('self._book.row["anchor_pk"]', True),
    ('body["in_reply_to_pk"]', False),
    ('meta.get("in_reply_to_pk")', False),
    ('row["message_id"]', False),
    ('{"in_reply_to_pk": None}', False),
])
def test_reading_a_stored_pk_by_name_is_seen(code, read):
    assert any(pk_column_is_read_by_name(n) for n in ast.walk(ast.parse(code))) is read
