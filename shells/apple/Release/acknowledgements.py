"""The Mac app's acknowledgements: every licence a release has to carry, as one text file.

    python3 -I acknowledgements.py OUT SPARKLE_LICENSE

release.sh runs it and puts OUT in the app as Contents/Resources/Acknowledgements.txt, where MailMind › Acknowledgements
shows it. Most of what MailMind ships is under licences (MIT, BSD, ISC, Apache, MPL) that ask for their notice to go
with every copy, the compiled ones too. It holds:

- MailMind's own licence, the repo's LICENSE.
- Every crate the core links into the app, for Apple silicon and Intel: what mm-ffi needs to build, its build
  dependencies too, since some carry the C sources they compile in (sqlcipher-src: SQLCipher and libtomcrypt). Each
  with every licence file the crate ships, those of the sources it vendors among them. Over-including a crate that
  only builds costs a few lines; leaving one out would ship code without its notice.
- Sparkle's LICENSE, which carries bsdiff's, sais-lite's and ed25519's notices.
- The typefaces the app carries (shells/apple/Fonts, made from the page's): each under the SIL Open Font License, which
  asks that its text and the face's copyright go with every copy of the fonts (web/assets/fonts/LICENSES).

A crate that ships no licence file gets the standard text of its licence from Release/licences, and the release stops
when there isn't one, rather than shipping the crate unacknowledged. The same text, a licence many crates share, is
written once, after the crates it covers.
"""

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CORE = REPO / "core"
TARGETS = ("aarch64-apple-darwin", "x86_64-apple-darwin")
NAMES = ("license", "licence", "copying", "notice", "unlicense", "copyright")
# Inside a crate, licences that cover only what never ships: its tests, benchmarks and examples.
SKIPPED = {"tests", "test", "benches", "examples", "fuzz", ".github"}
RULE = "-" * 78
# Each typeface's licence, as the page's fonts folder keeps it, and what the app sets in it.
FONTS = REPO / "web" / "assets" / "fonts" / "LICENSES"
TYPEFACES = {
    "NunitoSans.txt": "Nunito Sans, https://github.com/Fonthausen/NunitoSans: the interface and the mail",
    "ShipporiMincho.txt": "Shippori Mincho, https://github.com/fontdasu/ShipporiMincho: headings",
    "KleeOne.txt": "Klee One, https://github.com/fontworks-fonts/Klee: the brief",
}


def stop(words: str) -> None:
    print(f"acknowledgements: {words}", file=sys.stderr)
    sys.exit(1)


def metadata(target: str) -> dict:
    # Metadata resolves the whole workspace, including crates the release build did not fetch.
    said = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--filter-platform", target],
        cwd=CORE,
        capture_output=True,
        text=True,
        check=False,
    )
    if said.returncode != 0:
        stop(f"cargo metadata failed for {target}:\n{said.stderr}")
    return json.loads(said.stdout)


def shipped(meta: dict) -> list[dict]:
    """The packages mm-ffi needs to build: normal and build dependencies, never dev ones."""
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    roots = [p["id"] for p in meta["packages"] if p["name"] == "mm-ffi" and p["source"] is None]
    if len(roots) != 1:
        stop("the workspace has no mm-ffi")
    seen: set[str] = set()
    todo = roots
    while todo:
        package = todo.pop()
        if package in seen:
            continue
        seen.add(package)
        for dep in nodes[package]["deps"]:
            if any(kind["kind"] in (None, "build") for kind in dep["dep_kinds"]):
                todo.append(dep["pkg"])
    return [packages[p] for p in seen]


def licence_files(folder: Path) -> list[Path]:
    found = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part.lower() in SKIPPED for part in relative.parts[:-1]):
            continue
        if path.is_file() and path.name.lower().startswith(NAMES):
            found.append(path)
    return found


def standard_text(expression: str) -> list[Path]:
    """Release/licences' text for the licences an SPDX expression names: the first there is, for a choice (OR), or
    every one when the crate takes them together (AND, WITH)."""
    names = expression.replace("(", " ").replace(")", " ").replace("/", " OR ").split()
    licences = [n for n in names if n not in ("AND", "OR", "WITH")]
    texts = [HERE / "licences" / f"{n}.txt" for n in licences]
    if "AND" in names or "WITH" in names:
        return texts if texts and all(t.is_file() for t in texts) else []
    return [t for t in texts if t.is_file()][:1]


def main() -> None:
    if len(sys.argv) != 3:
        stop("usage: acknowledgements.py OUT SPARKLE_LICENSE")
    out, sparkle = Path(sys.argv[1]), Path(sys.argv[2])
    if not sparkle.is_file():
        stop(f"Sparkle's licence isn't at {sparkle}")
    for name in TYPEFACES:
        if not (FONTS / name).is_file():
            stop(f"the typeface's licence isn't at {FONTS / name}")

    crates: dict[tuple[str, str], dict] = {}
    for target in TARGETS:
        for package in shipped(metadata(target)):
            if package["source"] is not None:
                crates[(package["name"], package["version"])] = package

    # Each distinct licence text, with the crates it covers, in the order first met.
    covers: dict[str, list[str]] = defaultdict(list)
    for (name, version), package in sorted(crates.items()):
        folder = Path(package["manifest_path"]).parent
        files = licence_files(folder)
        expression = package.get("license") or ""
        if not files:
            files = standard_text(expression)
        if not files:
            stop(
                f"{name} {version} ships no licence file, and Release/licences has no text for '{expression}'. "
                "Add the licence's standard text there as <SPDX id>.txt."
            )
        label = f"{name} {version} ({expression})" if expression else f"{name} {version}"
        if package.get("repository"):
            label += f", {package['repository']}"
        for file in files:
            text = file.read_text(encoding="utf-8", errors="replace").strip()
            where = "" if file.parent == folder or file.parent == HERE / "licences" else f" ({file.relative_to(folder)})"
            covers[text].append(label + where)

    parts = [
        "MailMind's acknowledgements",
        "",
        "MailMind is built on the work of many others. Their licences ask that their notices go with every copy,",
        "and here they are: MailMind's own, Sparkle's, the typefaces', then each library the core is built from.",
        "",
        RULE,
        "MailMind, https://github.com/lanterno/mailmind",
        "",
        (REPO / "LICENSE").read_text(encoding="utf-8").strip(),
        "",
        RULE,
        "Sparkle, https://sparkle-project.org: updates",
        "",
        sparkle.read_text(encoding="utf-8").strip(),
    ]
    for name, label in TYPEFACES.items():
        parts += ["", RULE, label, "", (FONTS / name).read_text(encoding="utf-8").strip()]
    for text, labels in covers.items():
        parts += ["", RULE, *labels, "", text]
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"acknowledgements: {len(crates)} crates, {len(covers)} distinct licence texts, {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
