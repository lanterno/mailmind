"""The legacy ratchet: which legacy modules the mailmind package imports, checked against
tests/legacy_ratchet.txt (the file says how the list may change)."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RATCHET = ROOT / "tests" / "legacy_ratchet.txt"


def legacy_modules():
    """The app's modules at the repo root; the script test suites there aren't app code."""
    return {p.stem for p in ROOT.glob("*.py") if not p.name.startswith("test")}


def imported(path):
    """The top-level module each absolute import in a source file names."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def found():
    legacy, out = legacy_modules(), set()
    for path in sorted((ROOT / "mailmind").rglob("*.py")):
        parts = path.relative_to(ROOT).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        out |= {(module, name) for name in imported(path) if name in legacy}
    return out


def listed():
    lines = RATCHET.read_text(encoding="utf-8").splitlines()
    return {tuple(ln.split()) for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}


def test_the_legacy_modules_are_the_apps_root_modules():
    names = legacy_modules()
    assert {"server", "store", "outbox", "realtime"} <= names
    assert not {n for n in names if n.startswith("test")}


def test_every_line_names_a_mailmind_module_and_a_legacy_module():
    assert all(len(row) == 2 for row in listed()), sorted(r for r in listed() if len(r) != 2)
    assert {legacy for _, legacy in listed()} <= legacy_modules()


def test_the_package_imports_exactly_the_legacy_modules_listed():
    got, allowed = found(), listed()
    assert not got - allowed, (f"new imports of legacy modules; list them in {RATCHET.name} "
                               f"only with a reason: {sorted(got - allowed)}")
    assert not allowed - got, f"no longer imported; delete these lines from {RATCHET.name}: {sorted(allowed - got)}"


def test_only_bootstrap_reaches_the_legacy_modules():
    assert {m for m, _ in listed() if not m.startswith("mailmind.bootstrap")} == set()
