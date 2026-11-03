"""MailMind's new code, grown beside the legacy modules at the repo root during the refactor.

The legacy modules (server.py, store.py, ...) stay importable as before: both `import store` and
`import mailmind` resolve from the repo root while the two coexist. New code reaches a legacy
module only through `mailmind.bootstrap`; the legacy modules may import from here freely.

- `kernel`: the shared kernel. Standard library only, and nothing else from this package.
- `platform`: what every part of the app runs on. Today that is `platform.settings`.
"""
