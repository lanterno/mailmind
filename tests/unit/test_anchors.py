"""The end-to-end framework's anchors (tests/framework/anchors.toml) are the page's `data-mm` attributes, both ways: each
one listed is on the page where the list says, and the page carries no other. A scenario finds everything else by role
and label, so a new anchor is a choice made here, not a change that slips in."""
import os
import re
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANCHORS = os.path.join(ROOT, "tests", "framework", "anchors.toml")
SOURCES = os.path.join(ROOT, "web", "src")
ON_THE_PAGE = re.compile(r'data-mm="([a-z-]+)"')


def listed():
    with open(ANCHORS, "rb") as f:
        return tomllib.load(f)


def on_the_page():
    found = {}
    for folder, _, files in os.walk(SOURCES):
        for name in files:
            if name.endswith((".ts", ".tsx")) and ".test." not in name:
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    for anchor in ON_THE_PAGE.findall(f.read()):
                        found.setdefault(anchor, set()).add(os.path.relpath(path, ROOT))
    return found


def test_every_anchor_on_the_page_is_listed_and_every_one_listed_is_on_the_page():
    assert sorted(on_the_page()) == sorted(listed())


def test_each_anchor_is_where_its_entry_says_and_says_what_it_is_for():
    page = on_the_page()
    for name, entry in listed().items():
        assert entry.get("what"), f"{name}: say what it anchors, and why role and label can't find it"
        assert entry["page"] in page.get(name, set()), f"{name} isn't in {entry['page']}: {sorted(page.get(name, []))}"
