"""The limits in budgets.toml, and what a measured number means for a release.

Each entry is a table under its section (`[core.open_to_first_rows_ms]`), with `what` it measures, its `limit`, the
number last `measured` and where (`on`). An entry whose name ends in `_ms` is a wall time, which varies from run to run
on the same Mac, so it has a `tolerance`: 0.10 unless the entry gives its own. Every other entry (a size, a memory
footprint, a count) has none. A number is then:

- within: at or under its limit; the release goes;
- tolerated: over its limit by no more than the tolerance; the release goes and an issue is opened for the budget;
- over: past the limit and the tolerance; the release stops until the number is back or a commit raises the limit
  and says why.

An entry with `by = "hand"` has no script that measures it yet; its `how` says how it was measured. Each section's other
entries are written as `name=value` lines by the script that measures them (MEASURERS), and

    python tests/support/budgets.py judge apple build/measure/budgets.txt

judges such a file against the section: it prints each entry's verdict and exits 1 when one is over or wasn't measured.
"""
import math
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PATH = os.path.join(ROOT, "budgets.toml")

TOLERANCE = 0.10        # about the run-to-run noise of a wall time on a quiet Mac (docs/test-strategy.md, section 5)
MOST_TOLERANCE = 0.25   # past this an entry's own tolerance hides a real regression

# The files that measure each section, relative to the repository, and so must name each of its entries.
MEASURERS = {
    "core": ["core/crates/mm-budgets/src/main.rs"],
    "apple": ["shells/apple/Measure/measure.sh", "shells/apple/Measure/demo_home.py"],
    "web": ["shells/web/scripts/measure.mjs", "shells/web/justfile"],
    "desktop": ["shells/desktop/scripts/measure.mjs"],
    "counts": ["tests/api/test_budget_counts.py"],
}

NAME = re.compile(r"[a-z][a-z0-9_]*")

WITHIN, TOLERATED, OVER = "within", "tolerated", "over"


def load(path=PATH):
    """budgets.toml as {section: {name: entry}}."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def is_wall_time(name):
    return name.endswith("_ms")


def tolerance(name, entry):
    """The share of its limit a number may run over and still let a release go."""
    return entry.get("tolerance", TOLERANCE) if is_wall_time(name) else 0.0


def judge(name, entry, value):
    """WITHIN, TOLERATED or OVER, for one measured number."""
    limit = entry["limit"]
    if value <= limit:
        return WITHIN
    if value <= limit * (1 + tolerance(name, entry)):
        return TOLERATED
    return OVER


def problems(budgets):
    """What is wrong with the file itself: each entry's fields, its tolerance, its last measurement."""
    found = []
    for section, entries in budgets.items():
        if section not in MEASURERS:
            found.append(f"[{section}] is a section no script measures: add it to MEASURERS")
            continue
        for name, entry in entries.items():
            at = f"{section}.{name}"
            for field in ("what", "on"):
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    found.append(f"{at} has no `{field}`")
            limit = entry.get("limit")
            if not isinstance(limit, (int, float)) or isinstance(limit, bool) or not limit > 0:
                found.append(f"{at}: `limit` must be a number over 0")
                continue
            if section == "counts" and not isinstance(limit, int):
                found.append(f"{at}: a count's `limit` is a whole number")
            if "tolerance" in entry:
                t = entry["tolerance"]
                if not is_wall_time(name):
                    found.append(f"{at}: only a wall time (a name ending in _ms) has a tolerance")
                elif not isinstance(t, float) or not 0 < t <= MOST_TOLERANCE:
                    found.append(f"{at}: `tolerance` is a share over 0 and at most {MOST_TOLERANCE}")
            if entry.get("by", "script") not in ("script", "hand"):
                found.append(f"{at}: `by` is \"hand\" or left out")
            if entry.get("by") == "hand" and not entry.get("how"):
                found.append(f"{at} is measured by hand, so says `how`")
            measured = entry.get("measured")
            readings = measured.values() if isinstance(measured, dict) else [measured]
            for reading in readings:
                if not isinstance(reading, (int, float)) or isinstance(reading, bool):
                    found.append(f"{at}: `measured` is the number last recorded")
                elif judge(name, entry, reading) == OVER:
                    found.append(f"{at}: last measured {reading}, over its limit of {limit}")
    return found


def unread(budgets):
    """Entries a script should measure that none of their section's measurers names."""
    missing = []
    for section, entries in budgets.items():
        sources = ""
        for path in MEASURERS.get(section, []):
            try:
                with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                    sources += f.read()
            except FileNotFoundError:
                missing.append(f"{path}, which measures [{section}], doesn't exist")
        for name, entry in entries.items():
            if entry.get("by") != "hand" and name not in sources:
                missing.append(f"{section}.{name}: no measurer of [{section}] names it")
    return missing


def read_results(path):
    """`name=value` lines, as a measure script writes them; a later line for a name wins."""
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            name, sep, value = line.strip().partition("=")
            if sep and NAME.fullmatch(name):
                try:
                    values[name] = float(value)
                except ValueError:
                    pass                # a script's other output, or a number it couldn't take: not measured
    return values


def verdicts(section_entries, values):
    """(name, limit, value or None, verdict) for each entry of a section, in the file's order."""
    rows = []
    for name, entry in section_entries.items():
        value = values.get(name)
        if entry.get("by") == "hand":
            rows.append((name, entry["limit"], entry.get("measured"), "by hand"))
        elif value is None or math.isnan(value):
            rows.append((name, entry["limit"], None, "not measured"))
        else:
            rows.append((name, entry["limit"], value, judge(name, entry, value)))
    return rows


def main(argv):
    if len(argv) != 3 or argv[0] != "judge":
        sys.exit("usage: budgets.py judge SECTION RESULTS")
    section, results = argv[1], argv[2]
    entries = load().get(section)
    if not entries:
        sys.exit(f"budgets.toml has no [{section}]")
    rows = verdicts(entries, read_results(results))
    print(f"{'[' + section + ']':<26} {'limit':>9} {'measured':>10}  verdict")
    for name, limit, value, verdict in rows:
        shown = "-" if value is None else f"{value:.2f}"
        note = {
            TOLERATED: "  over, within its tolerance: the release goes; open an issue for this budget",
            OVER: "  OVER: the release stops",
            "not measured": "  NOT MEASURED: the release stops",
        }.get(verdict, "")
        print(f"{name:<26} {limit:>9} {shown:>10}  {verdict}{note}")
    stops = [r for r in rows if r[3] in (OVER, "not measured")]
    return 1 if stops else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
