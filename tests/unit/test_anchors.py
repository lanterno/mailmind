"""The end-to-end framework's anchors (tests/framework/anchors.toml) are the page's `data-mm` attributes and the Mac app's
`.accessibilityIdentifier`s, both ways: each one listed is in each client where the list says, and neither carries
another. A scenario finds everything else by role and label, so a new anchor is a choice made here, not a change that
slips in."""
import os
import re
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANCHORS = os.path.join(ROOT, "tests", "framework", "anchors.toml")
SOURCES = os.path.join(ROOT, "web", "src")
MAC = os.path.join(ROOT, "shells", "apple", "Sources", "Mac")
ON_THE_PAGE = re.compile(r'data-mm="([a-z-]+)"')
# A literal identifier: one made from a value (a conversation's id) is no anchor.
IN_THE_MAC = re.compile(r'\.accessibilityIdentifier\("([a-z -]+)"\)')


def listed():
    with open(ANCHORS, "rb") as f:
        return tomllib.load(f)


def _found(top, suffixes, pattern, skip=lambda name: False):
    found = {}
    for folder, _, files in os.walk(top):
        for name in files:
            if name.endswith(suffixes) and not skip(name):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    for anchor in pattern.findall(f.read()):
                        found.setdefault(anchor, set()).add(os.path.relpath(path, ROOT))
    return found


def on_the_page():
    return _found(SOURCES, (".ts", ".tsx"), ON_THE_PAGE, skip=lambda name: ".test." in name)


def in_the_mac():
    return _found(MAC, (".swift",), IN_THE_MAC)


def test_every_anchor_on_the_page_is_listed_and_every_one_listed_for_it_is_there():
    assert sorted(on_the_page()) == sorted(name for name, entry in listed().items() if entry["page"])


def test_every_anchor_in_the_mac_app_is_listed_and_every_one_listed_for_it_is_there():
    assert sorted(in_the_mac()) == sorted(name for name, entry in listed().items() if entry["mac"])


def test_each_anchor_is_where_its_entry_says_and_says_what_it_is_for():
    page, mac = on_the_page(), in_the_mac()
    for name, entry in listed().items():
        assert entry.get("what"), f"{name}: say what it anchors, and why role and label can't find it"
        assert entry["page"] or entry["mac"], f"{name}: an anchor in neither client"
        if entry["page"]:
            assert entry["page"] in page.get(name, set()), f"{name} isn't in {entry['page']}: {sorted(page.get(name, []))}"
        for path in entry["mac"] or []:
            assert path in mac.get(name, set()), f"{name} isn't in {path}: {sorted(mac.get(name, []))}"
        assert set(entry["mac"] or []) >= mac.get(name, set()), f"{name} is also in {sorted(mac.get(name, set()))}"
