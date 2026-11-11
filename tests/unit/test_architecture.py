"""The dependency rules the import graph can't hold: what's asked of the standard library, the Assistant's ports and who
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
    "assistant": ("ai_batch_items", "ai_batches", "ai_usage", "assessments", "briefs", "deltas", "draft_ahead", "reviews",
                  "thread_summaries"),
    "attention": ("followups", "muted", "snoozes", "tasks"),
    "calendar": (),
    "compose": ("drafts", "draft_journal", "outbox"),
    "curation": ("sender_unsub", "unsub_log"),
    "discovery": ("msg_fts",),
    "mailbox": ("attachments", "bodies", "message_identity", "message_labels", "message_recipients", "messages", "mail_commands", "sync_state", "thread_links"),
    "platform": ("documents", "undo_entries"),     # documents: each context's adapter names its own row
}

# Where another context still writes a table, each for a reason this list names. It may only shrink.
CROSSINGS = {
    ("msg_fts", "platform"),        # the upgrade check asks the index to check itself, on a copy
}

# What a domain may never ask the standard library for: it holds no I/O.
IO = {"sqlite3", "smtplib", "imaplib", "socket", "ssl", "http", "subprocess", "urllib.request"}

# All the other contexts' wiring may call of the Mailbox's store. A new crossing is an edit here and to the module, on purpose.
STORED_MAIL = {
    "any_copy", "brief_rows", "conversation_rows", "copy_in_folder", "copy_now", "delete_by_uids", "delete_message", "delta_rows",
    "draft_candidates", "get_body", "get_message", "get_message_by_key", "get_message_by_msgid", "get_uidvalidity",
    "handled_since", "message_stamp", "message_to_reply_to", "needs_you_rows", "queue_flags", "received_by_bucket",
    "received_since", "sender_recent", "sender_stats", "sent_since", "set_list_unsub", "set_local_flags", "thread_anchor_pk",
    "thread_stamps",
}


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


def test_only_the_tls_context_may_turn_a_certificates_checks_off():
    """Accounts' invariant: only for a loopback host, where a pin stands in. tests/security/test_hardening.py holds what
    tls_context does with a host, and that SMTP and IMAP each take it. No setting in the file reaches this, either."""
    skips = re.compile(r"CERT_NONE|CERT_OPTIONAL|check_hostname|_create_unverified_context|_create_stdlib_context")
    assert {rel(p) for p in modules() if skips.search(p.read_text(encoding="utf-8"))} == {"accounts/adapters/imap_connection.py"}


def test_only_safe_http_makes_a_request_by_hand():
    """The Claude API's go through the Anthropic SDK, which import-linter holds to its adapter."""
    calls = re.compile(r"\b(urlopen|HTTPSConnection|HTTPConnection)\(")
    assert {rel(p) for p in modules() if calls.search(p.read_text(encoding="utf-8"))} == {"curation/adapters/safe_http.py"}


def test_the_assistants_ports_only_read():
    """A plan's confirm runs through the mail's own contexts, so nothing the Assistant is handed can change mail."""
    from mailmind.bootstrap import assistant
    allowed = {"_Mail": {"fetch_body",                                 # fills a local cache, and writes to no server
                         "copy_now", "accounts", "get_message", "any_copy", "for_asking", "brief_rows", "delta_rows",
                         "sent_since", "received_since", "received_by_bucket", "needs_you_rows", "awaited_threads",
                         "replylater_due", "senders_cleaned", "draft_candidates", "thread_stamps", "message_stamp",
                         "thread_anchor_pk", "conversation_rows", "message_to_reply_to",
                         "sender_recent", "sender_stats", "screener_items", "handled_since", "events_from",
                         "weekly_review"},                                # a preference, read and never set
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


def test_the_package_opens_no_connection_outside_the_unit_of_work():
    """platform/db is what opens them. `connect` and `close` name other things here (an IMAP login, a socket), so a use case's
    list of names above says more than this one can."""
    by_hand = {"conn", "commit", "rollback"}
    found = {(rel(path), node.func.attr) for path in modules() if path.relative_to(PACKAGE).parts[:2] != ("platform", "db")
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in by_hand}
    assert found == set()


def test_nothing_takes_sqlite_from_the_standard_library():
    """The database is encrypted, and only SQLCipher's driver (platform/db/driver.py) reads it: the standard library's
    sqlite3 can't, and its errors are other classes, which an `except` would never see raised. The frozen migrations
    still name it, and migrations/__init__.py makes that name the driver."""
    frozen = PACKAGE / "platform" / "db" / "migrations" / "m0001_m0020.py"
    found = {rel(path) for path in sorted(PACKAGE.rglob("*.py")) if path != frozen and "sqlite3" in set(imports(path))}
    assert found == set()


def names_the_store(path):
    """Each way the file reaches the Mailbox's store by name, its package included. A word in a docstring is no reach."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name) and node.id == "store":
            yield node.id
        elif isinstance(node, ast.Attribute) and node.attr == "store":
            yield f"….{node.attr}"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            for alias in node.names:
                said = f"{module}.{alias.name}" if module else alias.name
                if alias.name == "store" or said.startswith("mailmind.mailbox.adapters.sqlite"):
                    yield said


def test_another_contexts_wiring_never_names_the_store():
    """It takes what `stored_mail` offers, below. The Mailbox's own wiring, the command line and the startup may name it."""
    wiring = [PACKAGE / "bootstrap" / f"{ctx}.py" for ctx in CONTEXTS if ctx != "mailbox"]
    assert all(p.is_file() for p in wiring)
    assert {(rel(p), said) for p in wiring for said in names_the_store(p)} == set()


def test_what_the_mailbox_offers_of_its_stored_mail_is_the_list_pinned():
    from mailmind.bootstrap import mailbox
    from mailmind.mailbox.adapters.sqlite import stored_mail
    assert mailbox.stored_mail is stored_mail
    assert {n for n in vars(stored_mail) if not n.startswith("__")} == STORED_MAIL
    assert sorted(stored_mail.__all__) == sorted(STORED_MAIL)


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


def assembled(module):
    """The names a context's wiring makes afresh in its assemble(): what it holds between requests."""
    tree = ast.parse((PACKAGE / "bootstrap" / f"{module}.py").read_text(encoding="utf-8"))
    return {name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "assemble"
            for g in ast.walk(node) if isinstance(g, ast.Global) for name in g.names}


def test_nothing_keeps_an_object_the_wiring_assembles_past_its_import():
    """wiring.assemble makes a new set of every context's objects, as each test does (tests/conftest.py's `_fresh_app`),
    so a name bound to one at the top of a module would keep the first set's. Code and tests alike read one through its
    module, `mailbox.ingest`, when they use it."""
    made = {m: assembled(m) for m in (*CONTEXTS, "background")}
    assert all(made[ctx] for ctx in CONTEXTS)
    bound = set()
    for p in [*PACKAGE.rglob("*.py"), *(ROOT / "tests").rglob("*.py")]:
        for node in ast.parse(p.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("mailmind.bootstrap."):
                module = node.module.rsplit(".", 1)[1]
                bound |= {(p.relative_to(ROOT).as_posix(), a.name) for a in node.names if a.name in made.get(module, ())}
    assert bound == set()
