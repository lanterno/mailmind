"""The dependency rules the import graph can't hold: what's asked of the standard library, the Assistant's ports, and who
writes which table. import-linter's contracts in pyproject.toml hold the rest."""
import ast
import pathlib
import re

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
    allowed = {"_Mail": {"fetch_body", "pk_now", "accounts", "get_message", "for_asking"},   # fetch_body fills a local cache, and writes to no server
               "_Search": {"parse", "reach", "sweeps_whole_mailbox"}}
    for name, methods in allowed.items():
        declared = {m for m in vars(getattr(assistant, name)) if not m.startswith("__")}
        assert declared == methods, name


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
