"""The composition root: what puts the app together and starts it.

This package is the one place in mailmind that imports the legacy modules at the repo root:
`legacy` the workers and the server, `mailbox` the connection's TLS, pin, login and credential.
tests/legacy_ratchet.txt lists each such import, so the list can only shrink as the refactor moves
code into the package.
"""
