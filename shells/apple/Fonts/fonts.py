"""Meadow's typefaces for the Apple app, made from the page's own: `just apple fonts`.

    uv run --no-project --with fonttools==4.68.0 --with brotli==1.2.1 python -I fonts.py PAGE_FONTS OUT

The page serves each face as Google Fonts cut it, one file per script (web/assets/fonts), and a browser picks a file by
the characters it needs. Core Text reads those WOFF2 files, but can't use them as they are: every file of a face has
the same PostScript name, so a name finds one of them (Nunito Sans' Latin Extended, which has no plain "a"), and the
family names are the server's, "Nunito Sans 12pt ExtraLight 12pt" and "Klee One SemiBold". So this joins each face's
files into one, by glyph name, gives it its family's own names, and makes Regular, not ExtraLight, Nunito Sans' default
weight. What it changes is allowed: the fonts are under the SIL Open Font License and reserve no names. It writes
TrueType, not WOFF2: Core Text reads both, but unpacking WOFF2 made registering the five take 25 ms as the app starts
rather than 6, and the .dmg compresses them either way.

Kerning and the other layout rules come from the Latin file, so letters only the other scripts have are spaced by
their advances alone.
"""

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.hvar import add_HVAR
from fontTools.varLib.instancer import instantiateVariableFont

# Each face the app carries: the page's files it is made from, Latin first, and the names it is known by.
FACES = {
    "NunitoSans-Regular": {
        "files": "nunito-sans-{}-wght-normal.woff2",
        "scripts": ["latin", "latin-ext", "vietnamese", "cyrillic", "cyrillic-ext"],
        "family": "Nunito Sans",
        "style": "Regular",
        "default_weight": 400,
    },
    "NunitoSans-Italic": {
        "files": "nunito-sans-{}-wght-italic.woff2",
        "scripts": ["latin", "latin-ext", "vietnamese", "cyrillic", "cyrillic-ext"],
        "family": "Nunito Sans",
        "style": "Italic",
        "default_weight": 400,
    },
    "ShipporiMincho-SemiBold": {
        "files": "shippori-mincho-{}-600-normal.woff2",
        "scripts": ["latin", "latin-ext"],
        "family": "Shippori Mincho",
        "style": "SemiBold",
    },
    "ShipporiMincho-Bold": {
        "files": "shippori-mincho-{}-700-normal.woff2",
        "scripts": ["latin", "latin-ext"],
        "family": "Shippori Mincho",
        "style": "Bold",
    },
    "KleeOne-SemiBold": {
        "files": "klee-one-{}-600-normal.woff2",
        "scripts": ["latin", "latin-ext", "cyrillic", "greek-ext"],
        "family": "Klee One",
        "style": "SemiBold",
    },
}


def stop(words: str) -> None:
    print(f"fonts: {words}", file=sys.stderr)
    sys.exit(1)


def join(base: TTFont, other: TTFont) -> None:
    """Add the glyphs `other` has and `base` lacks, with their outlines, advances, variations, characters and classes.
    Both are cuts of one font, so a glyph's name means the same glyph in each."""
    have = set(base.getGlyphOrder())
    added = [name for name in other.getGlyphOrder() if name not in have]
    glyf = base["glyf"]
    for name in added:
        # Reading a glyph through the table expands it, so a composite names its parts rather than numbering them.
        glyf[name] = other["glyf"][name]
        base["hmtx"].metrics[name] = other["hmtx"].metrics[name]
        if "vmtx" in base and "vmtx" in other:
            base["vmtx"].metrics[name] = other["vmtx"].metrics[name]
        if "gvar" in base:
            base["gvar"].variations[name] = other["gvar"].variations.get(name, [])
    base.setGlyphOrder(list(glyf.glyphOrder))

    classes = getattr(base["GDEF"].table, "GlyphClassDef", None) if "GDEF" in base else None
    theirs = getattr(other["GDEF"].table, "GlyphClassDef", None) if "GDEF" in other else None
    if classes is not None and theirs is not None:
        for name in added:
            if name in theirs.classDefs:
                classes.classDefs[name] = theirs.classDefs[name]

    characters = dict(other.getBestCmap())
    characters.update(base.getBestCmap())
    for table in base["cmap"].tables:
        if table.isUnicode():
            wide = table.format in (12, 13)
            table.cmap = {code: name for code, name in characters.items() if wide or code <= 0xFFFF}


def rename(font: TTFont, family: str, style: str, postscript: str) -> None:
    """The family's own names, as a typographic family with its styles: a style other than Regular, Italic, Bold and
    Bold Italic is a family of its own in the legacy names, as the face's upstream files have it."""
    legacy = style in ("Regular", "Italic", "Bold", "Bold Italic")
    version = font["name"].getDebugName(5) or ""
    names = {
        1: family if legacy else f"{family} {style}",
        2: style if legacy else "Regular",
        3: f"{version.split(';')[0].removeprefix('Version ')};NONE;{postscript}",
        4: f"{family} {style}",
        6: postscript,
        16: family,
        17: style,
    }
    if "fvar" in font:
        names[25] = family.replace(" ", "")
    table = font["name"]
    table.names = [n for n in table.names if n.nameID not in names]
    for name_id, text in names.items():
        table.setName(text, name_id, 3, 1, 0x409)
        table.setName(text, name_id, 1, 0, 0)


def make(page: Path, out: Path, name: str, face: dict) -> Path:
    files = [page / face["files"].format(script) for script in face["scripts"]]
    for file in files:
        if not file.is_file():
            stop(f"{file} isn't there")
    base = TTFont(files[0], lazy=False)
    base.ensureDecompiled()
    for file in files[1:]:
        other = TTFont(file, lazy=False)
        other.ensureDecompiled()
        join(base, other)

    if "fvar" in base:
        # The advances' variations are rebuilt from the outlines' once the glyphs are in: the Latin file's table
        # covers only its own glyphs.
        if "HVAR" in base:
            del base["HVAR"]
        axis = next(a for a in base["fvar"].axes if a.axisTag == "wght")
        weight = face["default_weight"]
        base = instantiateVariableFont(base, {"wght": (axis.minValue, weight, axis.maxValue)})
        base["OS/2"].usWeightClass = weight
        add_HVAR(base)
    base["OS/2"].recalcUnicodeRanges(base)
    base["OS/2"].updateFirstAndLastCharIndex(base)
    rename(base, face["family"], face["style"], name)

    base.flavor = None
    # The page's file's date stays, so making the fonts again from the same files makes the same bytes.
    base.recalcTimestamp = False
    path = out / f"{name}.ttf"
    base.save(path)
    return path


def main() -> None:
    if len(sys.argv) != 3:
        stop("usage: fonts.py PAGE_FONTS OUT")
    page, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for name, face in FACES.items():
        path = make(page, out, name, face)
        print(f"fonts: {path.name}, {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
