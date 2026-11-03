"""The composition root: what puts the app together and starts it.

`legacy` is the one module here, or anywhere in mailmind, that imports the legacy modules at the
repo root. tests/legacy_ratchet.txt lists each such import, so the list can only shrink as the
refactor moves code into the package.
"""
