"""CLAUDE.md's rules that a change can break without noticing, held over the sources (test-strategy.md section 6,
"Project rules as lints"). Each reads the code as it stands, Python by its syntax tree and Rust by its text with the
tests cut off, and names every place that breaks the rule. test_architecture.py holds the rules of layers and SQL."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "mailmind"
CRATES = ROOT / "core" / "crates"


def python_files():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def rust_sources(crate=None):
    """Each crate's own sources, as (path, text) with its tests cut off: a tests.rs, a tests/ folder, and everything from
    a file's `#[cfg(test)]` on."""
    for path in sorted((CRATES / crate if crate else CRATES).glob("**/src/**/*.rs")):
        if path.name == "tests.rs" or "tests" in path.relative_to(CRATES).parts[2:]:
            continue
        text = path.read_text(encoding="utf-8")
        yield path, text.split("#[cfg(test)]", 1)[0]


def where(path, line):
    return f"{path.relative_to(ROOT)}:{line}"


# ── never a bulk EXPUNGE ────────────────────────────────────────────────

def test_every_expunge_names_the_messages_it_removes():
    """A bare EXPUNGE removes every message marked \\Deleted in the folder, a person's own marks among them; MailMind
    removes only the copies it moved, by UID."""
    bare = []
    for path, tree in python_files():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "expunge":
                if not node.args and not node.keywords:
                    bare.append(where(path, node.lineno))
            # A command sent by its name (imaplib's `_simple_command("EXPUNGE")`); imaplib's `uid("EXPUNGE", …)` is
            # UID EXPUNGE, and `_ok` only names what answered.
            if isinstance(node, ast.Call) and _called(node) not in ("uid", "_ok") and any(
                    isinstance(a, ast.Constant) and isinstance(a.value, str) and re.search(r"\bEXPUNGE\b", a.value)
                    and "UID EXPUNGE" not in a.value for a in node.args):
                bare.append(where(path, node.lineno))
    for path, text in rust_sources("mm-imap"):
        for n, line in enumerate(text.splitlines(), 1):
            for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
                if re.search(r"\bEXPUNGE\b", literal) and "UID EXPUNGE" not in literal \
                        and not line.lstrip().startswith("const CHANGES"):     # the untagged answer's name, heard
                    bare.append(where(path, n))
    assert bare == []


def _called(call):
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


# ── a folder is quoted only by the one function that may ───────────────

def test_no_value_is_wrapped_in_quotes_by_hand_but_in_the_imap_quoting():
    """`f'"{folder}"'` puts a folder name on an IMAP line unchecked: a quote or a line break in it ends the string or
    the command. Folder names go through `mailbox_arg` and other values through `imap_quote` (Rust: `list::mailbox_arg`
    and `imap_quote`, and the session's `quote`)."""
    allowed = {("mailmind/mailbox/adapters/imap/imap_names.py", "mailbox_arg")}
    by_hand = []
    for path, tree in python_files():
        inside = {id(node) for function in ast.walk(tree)
                  if isinstance(function, ast.FunctionDef) and (str(path.relative_to(ROOT)), function.name) in allowed
                  for node in ast.walk(function)}
        by_hand += [where(path, node.lineno) for node in ast.walk(tree)
                    if isinstance(node, ast.JoinedStr) and _quote_wrapped(node) and id(node) not in inside]
    for path, text in rust_sources("mm-imap"):
        function = ""
        for n, line in enumerate(text.splitlines(), 1):
            named = re.search(r"\bfn (\w+)", line)
            function = named.group(1) if named else function
            if re.search(r'format!\(\s*"\\"\{', line) and (path.name, function) not in RUST_QUOTING:
                by_hand.append(where(path, n))
    assert sorted(set(by_hand)) == []


# The Rust core's quoting: a folder name as LIST gave it, a value escaped, and the session's login words.
RUST_QUOTING = {("list.rs", "mailbox_arg"), ("names.rs", "imap_quote"), ("session.rs", "quote")}


def _quote_wrapped(node):
    """An f-string that is a quote, one value and a quote, and nothing else."""
    v = node.values
    return (len(v) == 3 and isinstance(v[0], ast.Constant) and v[0].value == '"'
            and isinstance(v[1], ast.FormattedValue) and isinstance(v[2], ast.Constant) and v[2].value == '"')


# ── the environment is read in one place ────────────────────────────────

def test_only_the_environment_module_reads_the_environment():
    """A new variable is declared in `mailmind.platform.environment` and named in the README's table; anything else
    reading one is a setting no one can find."""
    readers = []
    for path, tree in python_files():
        if path == PACKAGE / "platform" / "environment.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv", "environb", "putenv") \
                    and isinstance(node.value, ast.Name) and node.value.id == "os":
                readers.append(where(path, node.lineno))
            if isinstance(node, ast.ImportFrom) and node.module == "os" \
                    and {a.name for a in node.names} & {"environ", "getenv", "environb", "putenv"}:
                readers.append(where(path, node.lineno))
    assert readers == []


# A program's own main reads the database key it is handed (core/CLAUDE.md, "Keys"); the server reads the rest in
# `environment`.
RUST_READERS = {"mm-server/src/environment.rs", "mm-daemon/src/main.rs", "mm-fixtures/src/main.rs"}


def test_only_the_rust_servers_environment_module_and_a_programs_main_read_a_variable():
    readers = []
    for path, text in rust_sources():
        if str(path.relative_to(CRATES)) in RUST_READERS:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"\benv::(var|vars|var_os|vars_os|set_var|remove_var)\b", line):
                readers.append(where(path, n))
    assert readers == []


# ── a log message is a literal ──────────────────────────────────────────

LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def test_every_log_message_is_a_literal():
    """A message is a literal and its values go in named fields that `ALLOWED` lets through: a message built from a
    value would carry whatever the value holds, a person's address or a subject among them, past the fields' check."""
    built = []
    for path, tree in python_files():
        loggers = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign) and _is_get_logger(node.value)
                   for target in node.targets if isinstance(target, ast.Name)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS):
                continue
            owner = node.func.value
            if not (_is_get_logger(owner) or (isinstance(owner, ast.Name) and owner.id in loggers)):
                continue
            message = node.args[1 if node.func.attr == "log" else 0] if node.args else None
            if not (isinstance(message, ast.Constant) and isinstance(message.value, str)):
                built.append(where(path, node.lineno))
    assert built == []


def _is_get_logger(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "get_logger"


# ── the checks see what they mean to ────────────────────────────────────

def test_the_checks_read_the_code_they_hold():
    """Each check above reads something: a rule over an empty tree would pass for nothing."""
    assert sum(1 for _ in python_files()) > 200
    assert sum(1 for _ in rust_sources("mm-imap")) > 5
    assert all((CRATES / reader).is_file() for reader in RUST_READERS)
    expunges = sum(1 for _, tree in python_files() for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "expunge")
    assert expunges >= 5
    logged = sum(1 for _, tree in python_files() for n in ast.walk(tree) if _is_get_logger(n))
    assert logged >= 40
